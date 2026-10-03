"""Trip roles and ``TripAccess``, the object-level guard of trip routes."""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.trips.models import Trip
from tuttitrip.trips.schemas import TripMembership, TripRead, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError, TripRoleError

BOB = AuthenticatedUser(sub="auth0|bob")
TRIP = uuid.uuid4()


def test_trip_roles_are_ordered() -> None:
    assert TripRole.HOST.satisfies(TripRole.CO_HOST)
    assert TripRole.CO_HOST.satisfies(TripRole.MEMBER)
    assert not TripRole.MEMBER.satisfies(TripRole.CO_HOST)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    authorize(app, BOB)  # feature permissions: everything
    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app) as test_client:
        yield test_client


def test_non_member_gets_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = TripNotFoundError(str(TRIP))
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=error))
    response = client.get(path("list_profiles", trip_id=TRIP))
    assert response.status_code == 404
    assert response.json() == {"detail": "Trip not found"}


def test_member_with_too_low_a_role_gets_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = TripRoleError("Trip role 'co_host' required (you are 'member')")
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=error))
    body = {"trip_id": str(TRIP), "request": "Gdańsk"}
    response = client.post(path("start_plan_job"), json=body)
    assert response.status_code == 403


def test_member_reads_the_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    membership = TripMembership(trip_id=TRIP, sub=BOB.sub, role=TripRole.MEMBER)
    check = AsyncMock(return_value=membership)
    monkeypatch.setattr(trip_service, "get_membership", check)
    monkeypatch.setattr(
        profile_service.db, "select_profiles_by_trip", AsyncMock(return_value=[])
    )
    response = client.get(path("list_profiles", trip_id=TRIP))
    assert response.status_code == 200
    assert response.json() == []
    _, trip_id, sub, min_role = check.call_args.args
    assert (trip_id, sub, min_role) == (TRIP, BOB.sub, TripRole.MEMBER)


def _trip(**overrides: object) -> Trip:
    values: dict[str, object] = {
        "id": TRIP,
        "owner_sub": BOB.sub,
        "name": "Gdańsk",
        "destination": None,
        "created_at": datetime(2026, 10, 3, tzinfo=UTC),
        "start_date": date(2026, 11, 1),
        "end_date": date(2026, 11, 3),
        "day_start": time(9),
        "day_end": time(19),
        "city_slug": None,
        "currency": None,
        "budget_total_min": None,
        "budget_total_max": None,
        "budget_day_min": None,
        "budget_day_max": None,
        "budget_flex_pct": 0,
        "fairness_alpha": 1.0,
    }
    return Trip(**(values | overrides))


def _as(
    monkeypatch: pytest.MonkeyPatch, role: TripRole, trip: Trip | None = None
) -> Trip:
    """Make the caller a ``role`` member of ``trip`` (stored in memory)."""
    trip = trip or _trip()
    membership = TripMembership(trip_id=TRIP, sub=BOB.sub, role=role)

    def check(
        _session: object, _trip_id: object, _sub: str, min_role: TripRole
    ) -> TripMembership:
        if not role.satisfies(min_role):
            msg = f"Trip role '{min_role}' required (you are '{role}')"
            raise TripRoleError(msg)
        return membership

    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=check))
    monkeypatch.setattr(trip_service.db, "select_trip", AsyncMock(return_value=trip))
    return trip


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def detail_client(session: AsyncMock) -> Iterator[TestClient]:
    app = create_app()
    authorize(app, BOB)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client


def test_outing_patch_is_read_back_with_its_day_window(
    detail_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _as(monkeypatch, TripRole.HOST)
    body = {"start_date": "2026-11-01", "end_date": "2026-11-01"}
    body |= {"day_start": "18:00:00", "day_end": "23:00:00"}
    response = detail_client.patch(path("update_trip", trip_id=TRIP), json=body)
    assert response.status_code == 200
    got = detail_client.get(path("get_trip", trip_id=TRIP)).json()
    assert got["kind"] == "outing"
    assert (got["day_start"], got["day_end"]) == ("18:00:00", "23:00:00")
    assert got["my_role"] == "host"


def test_multi_day_trip_is_a_trip_and_undated_one_too() -> None:
    assert TripRead.model_validate({**vars(_trip()), "my_role": "host"}).kind == "trip"
    undated = _trip(start_date=None, end_date=None)
    assert TripRead.model_validate({**vars(undated), "my_role": "host"}).kind == "trip"


def test_budget_range_and_flex_are_stored(
    detail_client: TestClient, monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    trip = _as(monkeypatch, TripRole.CO_HOST)
    body: dict[str, object] = {"budget_total_min": "1000"}
    body |= {"budget_total_max": "1500.50", "budget_flex_pct": 20, "currency": "PLN"}
    body["fairness_alpha"] = 2
    response = detail_client.patch(path("update_trip", trip_id=TRIP), json=body)
    assert response.status_code == 200
    assert trip.budget_total_max == Decimal("1500.50")
    assert trip.budget_flex_pct == 20
    session.commit.assert_awaited_once()


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"budget_total_min": "20", "budget_total_max": "10"}, "budget_total_max"),
        ({"budget_day_min": "20", "budget_day_max": "10"}, "budget_day_max"),
        ({"start_date": "2026-11-05", "end_date": "2026-11-04"}, "end_date"),
        ({"day_start": "19:00:00", "day_end": "09:00:00"}, "day_end"),
        ({"end_date": "2026-10-31"}, "end_date"),  # stored start_date is 2026-11-01
        ({"budget_flex_pct": 51}, "budget_flex_pct"),
        ({"fairness_alpha": 3.5}, "fairness_alpha"),
        ({"currency": "pln"}, "currency"),
        ({"city_slug": "Gdańsk"}, "city_slug"),
        ({"start_date": None}, "start_date"),  # stored dates are a pair
        ({"budget_total_min": "5"}, "budget_total_max"),  # a lone half of a range
        ({"budget_day_max": "5"}, "budget_day_min"),
        ({"day_start": None}, "day_start"),
    ],
)
def test_invalid_patch_is_422_naming_the_field(
    detail_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncMock,
    body: dict[str, object],
    field: str,
) -> None:
    trip = _as(monkeypatch, TripRole.HOST)
    response = detail_client.patch(path("update_trip", trip_id=TRIP), json=body)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == field
    assert trip.end_date == date(2026, 11, 3)
    session.commit.assert_not_awaited()


def test_clearing_both_dates_is_allowed(
    detail_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip = _as(monkeypatch, TripRole.HOST)
    body = {"start_date": None, "end_date": None}
    response = detail_client.patch(path("update_trip", trip_id=TRIP), json=body)
    assert response.status_code == 200
    assert trip.start_date is None
    assert trip.end_date is None


def test_patch_can_clear_a_nullable_field(
    detail_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip = _as(monkeypatch, TripRole.HOST, _trip(city_slug="gdansk"))
    response = detail_client.patch(
        path("update_trip", trip_id=TRIP), json={"city_slug": None}
    )
    assert response.status_code == 200
    assert trip.city_slug is None


def test_member_cannot_patch(
    detail_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _as(monkeypatch, TripRole.MEMBER)
    response = detail_client.patch(path("update_trip", trip_id=TRIP), json={})
    assert response.status_code == 403


def test_co_host_cannot_delete_but_host_can(
    detail_client: TestClient, monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    _as(monkeypatch, TripRole.CO_HOST)
    assert detail_client.delete(path("delete_trip", trip_id=TRIP)).status_code == 403
    session.commit.assert_not_awaited()

    removed = AsyncMock()
    monkeypatch.setattr(trip_service.db, "delete_trip", removed)
    _as(monkeypatch, TripRole.HOST)
    response = detail_client.delete(path("delete_trip", trip_id=TRIP))
    assert response.status_code == 204
    removed.assert_awaited_once_with(session, TRIP)
    session.commit.assert_awaited_once()


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_outsider_gets_404(
    detail_client: TestClient, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    error = TripNotFoundError(str(TRIP))
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=error))
    name = {"get": "get_trip", "patch": "update_trip", "delete": "delete_trip"}
    response = detail_client.request(method, path(name[method], trip_id=TRIP), json={})
    assert response.status_code == 404
