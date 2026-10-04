"""Check-ins: edit rules, expiry, routes and permissions, plus the SQL on Postgres."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.profiles.models import Profile
from tuttitrip.profiles.services import profile_service
from tuttitrip.profiles.services.profile_service import ProfileNotFoundError
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.db.session import database_url
from tuttitrip.shared.pagination.schemas import SortDir
from tuttitrip.trips.checkins import db as checkins_db
from tuttitrip.trips.checkins.logic.rules import can_edit, is_over
from tuttitrip.trips.checkins.models import TripCheckin
from tuttitrip.trips.checkins.schemas import CheckinQuery, CheckinSort, CheckinUpdate
from tuttitrip.trips.models import Trip
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import member_service, trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError

HOST, CO_HOST, MEMBER = TripRole.HOST, TripRole.CO_HOST, TripRole.MEMBER
TRIP = uuid.uuid4()
ME = AuthenticatedUser(sub="auth0|me")
BODY = {"accommodation": "Hotel Bristol", "room": "214"}


@pytest.mark.parametrize("role", list(TripRole))
def test_member_edits_only_own_entry(role: TripRole) -> None:
    assert can_edit(role, "a", "a") is True
    assert can_edit(role, "a", "b") is False


@pytest.mark.parametrize(
    ("role", "allowed"), [(HOST, True), (CO_HOST, False), (MEMBER, False)]
)
def test_only_host_edits_accountless_profiles(role: TripRole, allowed: bool) -> None:  # ruff: ignore[boolean-type-hint-positional-argument]
    assert can_edit(role, "a", None) is allowed


def test_trip_is_over_the_day_after_its_end() -> None:
    end = date(2026, 7, 10)
    assert is_over(end, None, end) is False
    assert is_over(end, None, end + timedelta(days=1)) is True
    assert is_over(None, None, date(2099, 1, 1)) is False


def test_without_end_date_the_trip_is_over_14_days_after_the_start() -> None:
    start = date(2026, 7, 1)
    assert is_over(None, start, start + timedelta(days=14)) is False
    assert is_over(None, start, start + timedelta(days=15)) is True


class State:
    """In-memory trip behind the mocked service and db layers."""

    def __init__(self) -> None:
        self.roles: dict[str, TripRole] = {}
        self.profiles: dict[uuid.UUID, SimpleNamespace] = {}
        self.rows: dict[uuid.UUID, TripCheckin] = {}
        self.end_date: date | None = None
        self.start_date: date | None = None

    def add(
        self, name: str, role: TripRole | None, sub: str | None = None
    ) -> SimpleNamespace:
        profile = SimpleNamespace(id=uuid.uuid4(), display_name=name, user_sub=sub)
        self.profiles[profile.id] = profile
        if role is not None and sub is not None:
            self.roles[sub] = role
        return profile


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> State:
    trip = State()

    def membership(
        _s: object, trip_id: uuid.UUID, sub: str, min_role: TripRole
    ) -> TripMembership:
        role = trip.roles.get(sub)
        if role is None:
            raise TripNotFoundError(str(trip_id))
        if not role.satisfies(min_role):
            raise trip_service.TripRoleError(str(min_role))
        return TripMembership(trip_id=trip_id, sub=sub, role=role)

    def get_profile(_s: object, _m: object, profile_id: uuid.UUID) -> SimpleNamespace:
        if profile_id not in trip.profiles:
            raise ProfileNotFoundError(str(profile_id))
        return trip.profiles[profile_id]

    def upsert(
        _s: object, trip_id: uuid.UUID, profile_id: uuid.UUID, data: CheckinUpdate
    ) -> TripCheckin:
        row = TripCheckin(
            trip_id=trip_id,
            profile_id=profile_id,
            accommodation=data.accommodation,
            room=data.room,
            updated_at=datetime.now(UTC),
        )
        trip.rows[profile_id] = row
        return row

    def select(_s: object, _t: uuid.UUID, query: CheckinQuery) -> SimpleNamespace:
        rows = sorted(trip.rows.values(), key=lambda r: r.accommodation)
        return SimpleNamespace(
            items=rows, total=len(rows), page=query.page, size=query.size, pages=1
        )

    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(side_effect=membership)
    )
    monkeypatch.setattr(
        trip_service,
        "get_trip",
        AsyncMock(
            side_effect=lambda *_: SimpleNamespace(
                end_date=trip.end_date, start_date=trip.start_date
            )
        ),
    )
    monkeypatch.setattr(
        profile_service, "get_profile", AsyncMock(side_effect=get_profile)
    )
    monkeypatch.setattr(
        profile_service,
        "list_profiles",
        AsyncMock(side_effect=lambda *_: list(trip.profiles.values())),
    )
    monkeypatch.setattr(checkins_db, "upsert_checkin", AsyncMock(side_effect=upsert))
    monkeypatch.setattr(checkins_db, "select_checkins", AsyncMock(side_effect=select))
    monkeypatch.setattr(
        checkins_db,
        "delete_checkin",
        AsyncMock(side_effect=lambda _s, _t, pid: trip.rows.pop(pid, None)),
    )
    monkeypatch.setattr(
        checkins_db,
        "delete_trip_checkins",
        AsyncMock(side_effect=lambda *_: trip.rows.clear()),
    )
    return trip


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


def _put(
    client: TestClient, profile: SimpleNamespace, body: dict[str, object] | None = None
) -> Response:
    return client.put(
        path("set_checkin", trip_id=TRIP, profile_id=profile.id), json=body or BODY
    )


def test_member_sets_own_entry_and_all_members_see_it(
    client: TestClient, state: State, session: AsyncMock
) -> None:
    mine = state.add("Ja", MEMBER, ME.sub)
    state.add("Ola", HOST, "auth0|ola")
    response = _put(client, mine)
    assert response.status_code == 200
    assert response.json()["room"] == "214"
    assert response.json()["is_me"] is True
    session.commit.assert_awaited_once()
    listing = client.get(path("list_checkins", trip_id=TRIP)).json()
    assert [(i["display_name"], i["accommodation"]) for i in listing["items"]] == [
        ("Ja", "Hotel Bristol")
    ]
    assert "user_sub" not in listing["items"][0]


def test_put_replaces_the_entry(client: TestClient, state: State) -> None:
    mine = state.add("Ja", MEMBER, ME.sub)
    _put(client, mine)
    _put(client, mine, {"accommodation": "Apartament"})
    assert len(state.rows) == 1
    assert state.rows[mine.id].accommodation == "Apartament"
    assert state.rows[mine.id].room is None


def test_outsider_gets_404_everywhere(client: TestClient, state: State) -> None:
    other = state.add("Ola", HOST, "auth0|ola")
    assert client.get(path("list_checkins", trip_id=TRIP)).status_code == 404
    assert _put(client, other).status_code == 404
    delete = client.delete(path("delete_checkin", trip_id=TRIP, profile_id=other.id))
    assert delete.status_code == 404


@pytest.mark.parametrize("role", [MEMBER, CO_HOST, HOST])
def test_cannot_change_someone_elses_entry(
    client: TestClient, state: State, session: AsyncMock, role: TripRole
) -> None:
    state.add("Ja", role, ME.sub)
    other = state.add("Ola", MEMBER, "auth0|ola")
    assert _put(client, other).status_code == 403
    delete = client.delete(path("delete_checkin", trip_id=TRIP, profile_id=other.id))
    assert delete.status_code == 403
    assert not state.rows
    session.commit.assert_not_awaited()


def test_host_fills_in_for_a_profile_without_account(
    client: TestClient, state: State
) -> None:
    state.add("Ja", HOST, ME.sub)
    kid = state.add("Zosia", None)
    assert _put(client, kid).status_code == 200


def test_cohost_cannot_fill_in_for_a_profile_without_account(
    client: TestClient, state: State
) -> None:
    state.add("Ja", CO_HOST, ME.sub)
    assert _put(client, state.add("Zosia", None)).status_code == 403


def test_unknown_profile_is_404(client: TestClient, state: State) -> None:
    state.add("Ja", MEMBER, ME.sub)
    ghost = SimpleNamespace(id=uuid.uuid4())
    assert _put(client, ghost).status_code == 404


def test_delete_removes_own_entry(client: TestClient, state: State) -> None:
    mine = state.add("Ja", MEMBER, ME.sub)
    _put(client, mine)
    response = client.delete(path("delete_checkin", trip_id=TRIP, profile_id=mine.id))
    assert response.status_code == 204
    assert not state.rows


def test_entries_are_gone_after_the_trip_ends(client: TestClient, state: State) -> None:
    mine = state.add("Ja", MEMBER, ME.sub)
    _put(client, mine)
    state.end_date = datetime.now(UTC).date() - timedelta(days=1)
    listing = client.get(path("list_checkins", trip_id=TRIP)).json()
    assert listing["items"] == []
    assert listing["total"] == 0
    assert not state.rows
    assert _put(client, mine).status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"accommodation": ""},
        {"accommodation": "x" * 201},
        {"accommodation": "Hotel", "room": "r" * 21},
        {"room": "1"},
        {"accommodation": "Hotel", "extra": 1},
    ],
)
def test_invalid_body_is_422(
    client: TestClient, state: State, body: dict[str, object]
) -> None:
    mine = state.add("Ja", MEMBER, ME.sub)
    assert _put(client, mine, body).status_code == 422


@pytest.mark.integration
def test_sql_upsert_filter_sort_and_cascade() -> None:
    """Runs against the local Postgres (`alembic upgrade head` done)."""

    async def run() -> None:
        engine = create_async_engine(database_url(get_settings().database))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            trip = Trip(owner_sub="auth0|it", name="Checkin IT")
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
                for n in ("A", "B")
            ]
            session.add_all(people)
            await session.flush()
            a, b = people
            await checkins_db.upsert_checkin(
                session, trip.id, a.id, CheckinUpdate(accommodation="Zeta", room="1")
            )
            await checkins_db.upsert_checkin(
                session, trip.id, a.id, CheckinUpdate(accommodation="Beta")
            )
            await checkins_db.upsert_checkin(
                session, trip.id, b.id, CheckinUpdate(accommodation="alfa", room="9")
            )
            page = await checkins_db.select_checkins(
                session, trip.id, CheckinQuery(sort=CheckinSort.ACCOMMODATION)
            )
            assert [r.accommodation for r in page.items] == ["alfa", "Beta"]
            assert page.items[1].room is None
            desc = await checkins_db.select_checkins(
                session, trip.id, CheckinQuery(dir=SortDir.DESC, size=1)
            )
            assert (desc.total, desc.pages) == (2, 2)
            only = await checkins_db.select_checkins(
                session, trip.id, CheckinQuery(accommodation="BET")
            )
            assert [r.profile_id for r in only.items] == [a.id]
            await checkins_db.delete_checkin(session, trip.id, b.id)
            await checkins_db.delete_checkin(session, trip.id, b.id)
            left = await checkins_db.select_checkins(session, trip.id, CheckinQuery())
            assert [r.profile_id for r in left.items] == [a.id]
            await session.delete(trip)
            await session.commit()
        await engine.dispose()

    asyncio.run(run())


def test_entries_are_gone_after_14_days_when_the_trip_has_no_end_date(
    client: TestClient, state: State
) -> None:
    mine = state.add("Ja", MEMBER, ME.sub)
    _put(client, mine)
    state.start_date = datetime.now(UTC).date() - timedelta(days=15)
    assert client.get(path("list_checkins", trip_id=TRIP)).json()["items"] == []
    assert not state.rows


def test_responses_are_not_cached(client: TestClient, state: State) -> None:
    mine = state.add("Ja", MEMBER, ME.sub)
    assert _put(client, mine).headers["cache-control"] == "no-store"
    listing = client.get(path("list_checkins", trip_id=TRIP))
    assert listing.headers["cache-control"] == "no-store"


@pytest.mark.usefixtures("client")
def test_a_member_who_left_loses_the_room_number(state: State) -> None:
    mine = state.add("Zosia", MEMBER, "auth0|zosia")
    state.rows[mine.id] = TripCheckin(
        trip_id=TRIP, profile_id=mine.id, accommodation="Hotel", room="1"
    )
    asyncio.run(member_service.member_left(AsyncMock(), TRIP, mine.id, "auth0|zosia"))
    assert mine.id not in state.rows
