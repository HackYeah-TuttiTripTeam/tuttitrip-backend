"""Location sharing: opt-in, expiry, privacy and the SQL on Postgres."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.profiles.models import Profile
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.db.session import database_url
from tuttitrip.trips.locations import db as locations_db
from tuttitrip.trips.locations.models import TripLocation, TripLocationConsent
from tuttitrip.trips.locations.schemas import LocationQuery
from tuttitrip.trips.models import Trip
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import member_service, trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError

TRIP = uuid.uuid4()
ME = AuthenticatedUser(sub="auth0|me")
MY_PROFILE = uuid.uuid4()
OLA_PROFILE = uuid.uuid4()
POSITION = {"latitude": 50.06, "longitude": 19.94, "accuracy_m": 12.5}


class State:
    """In-memory consents and positions behind the mocked db layer."""

    def __init__(self) -> None:
        self.member = True
        self.consent: datetime | None = None
        self.rows: dict[uuid.UUID, TripLocation] = {}
        self.purged = 0


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> State:
    st = State()

    def membership(
        _s: object, trip_id: uuid.UUID, sub: str, _min: TripRole
    ) -> TripMembership:
        if not st.member:
            raise TripNotFoundError(str(trip_id))
        return TripMembership(trip_id=trip_id, sub=sub, role=TripRole.MEMBER)

    def consent(
        _s: object, _t: uuid.UUID, _p: uuid.UUID, now: datetime
    ) -> TripLocationConsent | None:
        if st.consent is None or st.consent <= now:
            return None
        return TripLocationConsent(
            trip_id=TRIP, profile_id=MY_PROFILE, until=st.consent
        )

    def upsert(
        _s: object, trip_id: uuid.UUID, profile_id: uuid.UUID, **kw: object
    ) -> TripLocation:
        kw["recorded_at"] = kw.pop("now")
        row = TripLocation(trip_id=trip_id, profile_id=profile_id, **kw)
        st.rows[profile_id] = row
        return row

    def select(*_: object) -> SimpleNamespace:
        rows = list(st.rows.values())
        return SimpleNamespace(items=rows, total=len(rows), page=1, size=20, pages=1)

    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(side_effect=membership)
    )
    monkeypatch.setattr(
        profile_service, "find_account_profile", AsyncMock(return_value=MY_PROFILE)
    )
    monkeypatch.setattr(
        profile_service,
        "list_profiles",
        AsyncMock(
            return_value=[
                SimpleNamespace(id=MY_PROFILE, user_sub=ME.sub, display_name="Ja"),
                SimpleNamespace(
                    id=OLA_PROFILE, user_sub="auth0|ola", display_name="Ola"
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        profile_service,
        "get_profile",
        AsyncMock(
            return_value=SimpleNamespace(
                id=MY_PROFILE, user_sub=ME.sub, display_name="Ja"
            )
        ),
    )
    monkeypatch.setattr(
        locations_db, "select_active_consent", AsyncMock(side_effect=consent)
    )
    monkeypatch.setattr(
        locations_db,
        "upsert_consent",
        AsyncMock(side_effect=lambda _s, _t, _p, until: setattr(st, "consent", until)),
    )
    monkeypatch.setattr(locations_db, "upsert_position", AsyncMock(side_effect=upsert))
    monkeypatch.setattr(locations_db, "select_locations", AsyncMock(side_effect=select))
    monkeypatch.setattr(
        locations_db,
        "purge_expired",
        AsyncMock(side_effect=lambda *_: setattr(st, "purged", st.purged + 1)),
    )

    def delete(*_: object) -> None:
        st.consent = None
        st.rows.pop(MY_PROFILE, None)

    monkeypatch.setattr(
        locations_db, "delete_for_profile", AsyncMock(side_effect=delete)
    )
    return st


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client(session: AsyncMock) -> Iterator[TestClient]:
    app: FastAPI = create_app()
    authorize(app, ME)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client


def _p(name: str) -> str:
    return path(name, trip_id=TRIP)


@pytest.mark.usefixtures("state")
def test_default_is_off(client: TestClient) -> None:
    response = client.get(_p("get_my_consent"))
    assert response.json() == {"enabled": False, "until": None}
    assert response.headers["cache-control"] == "no-store"


def test_position_without_consent_is_refused_and_not_stored(
    client: TestClient, state: State
) -> None:
    response = client.put(_p("update_my_position"), json=POSITION)
    assert response.status_code == 403
    assert not state.rows
    locations_db.upsert_position.assert_not_awaited()  # ty: ignore[unresolved-attribute]


def test_lapsed_consent_counts_as_off(client: TestClient, state: State) -> None:
    state.consent = datetime.now(UTC) - timedelta(minutes=1)
    assert client.put(_p("update_my_position"), json=POSITION).status_code == 403
    assert not state.rows


@pytest.mark.usefixtures("state")
def test_consent_then_position_is_stored_with_expiry(client: TestClient) -> None:
    on = client.put(_p("set_my_consent"), json={"duration_minutes": 60})
    assert on.status_code == 200
    assert on.json()["enabled"] is True
    response = client.put(_p("update_my_position"), json=POSITION)
    assert response.status_code == 200
    body = response.json()
    assert body["is_me"] is True
    assert body["display_name"] == "Ja"
    recorded = datetime.fromisoformat(body["recorded_at"])
    expires = datetime.fromisoformat(body["expires_at"])
    assert expires - recorded == timedelta(minutes=15)
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.usefixtures("state")
def test_position_never_outlives_the_consent(client: TestClient) -> None:
    client.put(_p("set_my_consent"), json={"duration_minutes": 5})
    body = client.put(_p("update_my_position"), json=POSITION).json()
    expires = datetime.fromisoformat(body["expires_at"])
    assert expires <= datetime.now(UTC) + timedelta(minutes=5)


def test_stop_deletes_consent_and_position(client: TestClient, state: State) -> None:
    client.put(_p("set_my_consent"), json={})
    client.put(_p("update_my_position"), json=POSITION)
    assert client.delete(_p("stop_sharing")).status_code == 204
    assert state.consent is None
    assert not state.rows
    assert client.put(_p("update_my_position"), json=POSITION).status_code == 403


def test_list_purges_first_and_names_come_from_profiles(
    client: TestClient, state: State
) -> None:
    client.put(_p("set_my_consent"), json={})
    client.put(_p("update_my_position"), json=POSITION)
    purged = state.purged
    response = client.get(_p("list_locations"))
    assert response.status_code == 200
    assert state.purged == purged + 1
    item = response.json()["items"][0]
    assert item["display_name"] == "Ja"
    assert "user_sub" not in item
    assert response.headers["cache-control"] == "no-store"


def test_outsider_gets_404_everywhere(client: TestClient, state: State) -> None:
    state.member = False
    assert client.get(_p("list_locations")).status_code == 404
    assert client.get(_p("get_my_consent")).status_code == 404
    assert client.put(_p("set_my_consent"), json={}).status_code == 404
    assert client.put(_p("update_my_position"), json=POSITION).status_code == 404
    assert client.delete(_p("stop_sharing")).status_code == 404
    assert not state.rows
    assert state.consent is None


def test_removed_member_loses_consent_and_position(
    client: TestClient, state: State
) -> None:
    client.put(_p("set_my_consent"), json={})
    client.put(_p("update_my_position"), json=POSITION)
    assert state.rows
    asyncio.run(member_service.member_left(AsyncMock(), TRIP, MY_PROFILE))
    assert state.consent is None
    assert not state.rows


@pytest.mark.parametrize(
    "body",
    [
        {"latitude": 91, "longitude": 0},
        {"latitude": 0, "longitude": 181},
        {"latitude": 0, "longitude": 0, "accuracy_m": -1},
        {"latitude": 0},
        {"latitude": 0, "longitude": 0, "name": "x"},
    ],
)
def test_invalid_position_is_422(
    client: TestClient, state: State, body: dict[str, object]
) -> None:
    state.consent = datetime.now(UTC) + timedelta(hours=1)
    assert client.put(_p("update_my_position"), json=body).status_code == 422
    assert not state.rows


@pytest.mark.parametrize("minutes", [0, 4, 1441])
def test_consent_duration_is_bounded(
    client: TestClient, state: State, minutes: int
) -> None:
    response = client.put(_p("set_my_consent"), json={"duration_minutes": minutes})
    assert response.status_code == 422
    assert state.consent is None


@pytest.mark.integration
def test_sql_visibility_purge_and_cascade() -> None:
    """Runs against the local Postgres (`alembic upgrade head` done)."""

    async def run() -> None:
        engine = create_async_engine(database_url(get_settings().database))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(UTC)
        async with sessions() as session:
            trip = Trip(owner_sub="auth0|it", name="Locations IT")
            session.add(trip)
            await session.flush()
            people = [
                Profile(
                    trip_id=trip.id,
                    display_name=n,
                    age=30,
                    segment_km=1,
                    daily_km=1,
                    active_min=1,
                    stairs_sensitivity=0,
                    queue_patience_min=1,
                )
                for n in ("ok", "expired", "nocons", "lapsed")
            ]
            session.add_all(people)
            await session.flush()
            ok, expired, nocons, lapsed = (p.id for p in people)
            hour = now + timedelta(hours=1)
            for pid, until in (
                (ok, hour),
                (expired, hour),
                (lapsed, now - timedelta(minutes=1)),
            ):
                await locations_db.upsert_consent(session, trip.id, pid, until)
            for pid, exp in (
                (ok, now + timedelta(minutes=10)),
                (expired, now - timedelta(minutes=1)),
                (nocons, now + timedelta(minutes=10)),
                (lapsed, now + timedelta(minutes=10)),
            ):
                await locations_db.upsert_position(
                    session,
                    trip.id,
                    pid,
                    latitude=1.0,
                    longitude=2.0,
                    accuracy_m=None,
                    now=now,
                    expires_at=exp,
                )
            await locations_db.upsert_position(
                session,
                trip.id,
                ok,
                latitude=3.0,
                longitude=4.0,
                accuracy_m=5.0,
                now=now,
                expires_at=now + timedelta(minutes=10),
            )
            await session.commit()
            page = await locations_db.select_locations(
                session, trip.id, ok, now, LocationQuery()
            )
            assert [(r.profile_id, r.latitude) for r in page.items] == [(ok, 3.0)]
            mine = await locations_db.select_locations(
                session, trip.id, ok, now, LocationQuery(mine=False)
            )
            assert mine.items == []
            await locations_db.purge_expired(session, trip.id, now)
            await session.commit()
            left = await locations_db.select_locations(
                session, trip.id, ok, now, LocationQuery()
            )
            assert len(left.items) == 1
            raw = (
                await session.execute(
                    TripLocation.__table__.select().where(
                        TripLocation.trip_id == trip.id
                    )
                )
            ).all()
            assert [r.profile_id for r in raw] == [ok]
            await locations_db.delete_for_profile(session, trip.id, ok)
            assert (
                await locations_db.select_active_consent(session, trip.id, ok, now)
                is None
            )
            await session.delete(trip)
            await session.commit()
        await engine.dispose()

    asyncio.run(run())
