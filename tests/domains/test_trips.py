"""Trip roles and ``TripAccess``, the object-level guard of trip routes."""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.planning.parameters.services import parameters_service
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.pagination.schemas import Page, PageParams, SortDir
from tuttitrip.trips import db as trips_db
from tuttitrip.trips.models import Trip
from tuttitrip.trips.schemas import (
    MemberStatus,
    TripFilter,
    TripMembership,
    TripRead,
    TripRole,
    TripSort,
    TripWhen,
)
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
        "propose_cheaper_alternatives": True,
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
    assert (
        TripRead.model_validate(
            {**vars(_trip()), "my_role": "host", "my_status": "confirmed"}
        ).kind
        == "trip"
    )
    undated = _trip(start_date=None, end_date=None)
    assert (
        TripRead.model_validate(
            {**vars(undated), "my_role": "host", "my_status": "confirmed"}
        ).kind
        == "trip"
    )


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


def test_cheaper_alternatives_are_on_by_default_and_can_be_switched_off(
    detail_client: TestClient, monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    trip = _as(monkeypatch, TripRole.CO_HOST)
    assert detail_client.get(path("get_trip", trip_id=TRIP)).json()[
        "propose_cheaper_alternatives"
    ]
    body = {"propose_cheaper_alternatives": False}
    response = detail_client.patch(path("update_trip", trip_id=TRIP), json=body)
    assert response.status_code == 200
    assert response.json()["propose_cheaper_alternatives"] is False
    assert trip.propose_cheaper_alternatives is False
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
        ({"propose_cheaper_alternatives": None}, "propose_cheaper_alternatives"),
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


FULL: dict[str, object] = {
    "name": "Gdańsk",
    "destination": "Gdańsk",
    "start_date": "2026-11-01",
    "end_date": "2026-11-03",
    "day_start": "08:30:00",
    "day_end": "20:00:00",
    "city_slug": "gdansk",
    "currency": "PLN",
    "budget_total_min": "1000",
    "budget_total_max": "1500.50",
    "budget_day_min": "100",
    "budget_day_max": "300",
    "budget_flex_pct": 20,
    "fairness_alpha": 2,
}


DEFAULT_ALPHA = 1.5


def _post_client(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> tuple[TestClient, AsyncMock, AsyncMock]:
    insert = AsyncMock(return_value=_trip())
    host = AsyncMock()
    monkeypatch.setattr(trip_service.db, "insert_trip", insert)
    monkeypatch.setattr(profile_service, "create_host_profile", host)
    monkeypatch.setattr(
        parameters_service, "default_alpha", AsyncMock(return_value=DEFAULT_ALPHA)
    )
    app = create_app()
    authorize(app, BOB)
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app), insert, host


def test_post_creates_a_full_trip_in_one_request(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    client, insert, host = _post_client(monkeypatch, session)
    response = client.post(path("create_trip"), json=FULL)
    assert response.status_code == 201
    fields = insert.call_args.kwargs["fields"]
    assert fields["start_date"] == date(2026, 11, 1)
    assert fields["day_start"] == time(8, 30)
    assert fields["budget_total_max"] == Decimal("1500.50")
    assert fields["budget_flex_pct"] == 20
    assert fields["city_slug"] == "gdansk"
    assert set(fields) == set(FULL)
    host.assert_awaited_once()  # host profile in the same transaction
    session.commit.assert_awaited_once()


def test_post_with_only_name_and_destination_still_works(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    client, insert, _ = _post_client(monkeypatch, session)
    body = {"name": "X", "destination": "Y"}
    assert client.post(path("create_trip"), json=body).status_code == 201
    # The administrator's default slider is filled in when the request has none.
    assert insert.call_args.kwargs["fields"] == {
        **body,
        "fairness_alpha": DEFAULT_ALPHA,
    }
    assert client.post(path("create_trip"), json={"name": "X"}).status_code == 201


@pytest.mark.parametrize(
    ("extra", "field", "code"),
    [
        (
            {"start_date": "2026-11-05", "end_date": "2026-11-04"},
            "end_date",
            "dates_order",
        ),
        (
            {"budget_total_min": "20", "budget_total_max": "10"},
            "budget_total_max",
            "budget_order",
        ),
        (
            {"budget_day_min": "20", "budget_day_max": "10"},
            "budget_day_max",
            "budget_order",
        ),
        (
            {"day_start": "19:00:00", "day_end": "09:00:00"},
            "day_end",
            "day_window_order",
        ),
        ({"start_date": "2026-11-05"}, "end_date", "pair_required"),
        ({"end_date": "2026-11-05"}, "start_date", "pair_required"),
        ({"budget_total_min": "5"}, "budget_total_max", "pair_required"),
        ({"budget_day_max": "5"}, "budget_day_min", "pair_required"),
        ({"day_start": None}, "day_start", "null_not_allowed"),
    ],
)
def test_post_errors_have_the_field_and_a_stable_code(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncMock,
    extra: dict[str, object],
    field: str,
    code: str,
) -> None:
    client, insert, host = _post_client(monkeypatch, session)
    response = client.post(path("create_trip"), json={"name": "X", **extra})
    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert error["loc"] == ["body", field]
    assert error["type"] == f"trip.{code}"  # survives the handler that strips ctx
    assert set(error) == {"type", "loc", "msg"}
    insert.assert_not_awaited()
    host.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_post_reports_all_broken_rules_at_once(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    client, _, _ = _post_client(monkeypatch, session)
    body = {"name": "X", "start_date": "2026-11-05", "end_date": "2026-11-04"}
    body |= {"day_start": "19:00:00", "day_end": "09:00:00"}
    response = client.post(path("create_trip"), json=body)
    types = {e["type"] for e in response.json()["detail"]}
    assert types == {"trip.dates_order", "trip.day_window_order"}


def test_patch_errors_carry_the_same_codes(
    detail_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _as(monkeypatch, TripRole.HOST)
    cases = [
        ({"end_date": "2026-10-31"}, "trip.dates_order"),  # vs the stored start
        ({"start_date": None}, "trip.pair_required"),  # stored end remains
        ({"budget_total_min": "20", "budget_total_max": "10"}, "trip.budget_order"),
        ({"day_start": None}, "trip.null_not_allowed"),
        ({"day_start": "19:00:00", "day_end": "09:00:00"}, "trip.day_window_order"),
        ({"budget_day_min": "5"}, "trip.pair_required"),
    ]
    for body, code in cases:
        response = detail_client.patch(path("update_trip", trip_id=TRIP), json=body)
        assert response.status_code == 422
        assert response.json()["detail"][0]["type"] == code


def test_failing_host_profile_rolls_back_the_trip(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    client, _, host = _post_client(monkeypatch, session)
    host.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        client.post(path("create_trip"), json={"name": "X"})
    session.commit.assert_not_awaited()


def test_openapi_lists_the_trip_error_codes() -> None:
    schema = create_app().openapi()
    schemas = schema["components"]["schemas"]
    assert "trip.dates_order" in schemas["TripErrorCode"]["enum"]
    for method, route in (
        ("post", "/api/v1/trips"),
        ("patch", "/api/v1/trips/{trip_id}"),
    ):
        content = schema["paths"][route][method]["responses"]["422"]["content"]
        ref = content["application/json"]["schema"]["$ref"]
        assert ref.endswith("/TripValidationErrors")


@pytest.fixture
def list_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, AsyncMock]:
    listing = AsyncMock(return_value=Page[TripRead].of([], 0, PageParams()))
    monkeypatch.setattr(trip_service, "list_trips", listing)
    app = create_app()
    authorize(app, BOB)
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app), listing


def test_list_defaults_and_reads_the_filters(
    list_client: tuple[TestClient, AsyncMock],
) -> None:
    client, listing = list_client
    response = client.get(
        path("list_trips"),
        params=[
            ("role", "host"),
            ("role", "member"),
            ("q", "kra"),
            ("start_from", "2026-11-01"),
            ("sort", "start_date"),
        ],
    )
    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "size": 20,
        "pages": 0,
    }
    query = listing.await_args_list[0].args[2]
    assert (query.page, query.size, query.sort, query.dir) == (
        1,
        20,
        TripSort.START_DATE,
        SortDir.DESC,
    )
    assert query.role == [TripRole.HOST, TripRole.MEMBER]
    assert (query.q, query.start_from) == ("kra", date(2026, 11, 1))


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "owner_sub"},
        {"dir": "sideways"},
        {"size": 101},
        {"page": 0},
        {"role": "boss"},
        {"kind": "holiday"},
        {"city": "Kraków"},
        {"q": ""},
        {"start_from": "2026-12-01", "start_to": "2026-11-01"},
        {"unknown": "1"},
    ],
)
def test_list_rejects_bad_params(
    list_client: tuple[TestClient, AsyncMock], params: dict[str, object]
) -> None:
    client, listing = list_client
    assert client.get(path("list_trips"), params=params).status_code == 422  # ty: ignore[invalid-argument-type]
    listing.assert_not_awaited()


def _filters_sql(**params: object) -> str:
    stmt = trips_db.apply_filters(
        trips_db.scoped("auth0|x"), TripFilter.model_validate(params)
    )
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_past_means_ended_before_today_and_upcoming_includes_undated() -> None:
    past = _filters_sql(when="past")
    assert "trips.end_date < CURRENT_DATE" in past
    upcoming = _filters_sql(when="upcoming")
    assert "trips.end_date IS NULL OR trips.end_date >= CURRENT_DATE" in upcoming
    assert "CURRENT_DATE" not in _filters_sql()


def test_status_filter_uses_the_callers_membership_row() -> None:
    assert "trip_members.status = %(status_1)s" in _filters_sql(status="pending")


@pytest.mark.parametrize("params", [{"when": "never"}, {"status": "maybe"}])
def test_list_rejects_bad_when_and_status(
    list_client: tuple[TestClient, AsyncMock], params: dict[str, str]
) -> None:
    client, listing = list_client
    assert client.get(path("list_trips"), params=params).status_code == 422
    listing.assert_not_awaited()


def test_list_reads_when_and_status(
    list_client: tuple[TestClient, AsyncMock],
) -> None:
    client, listing = list_client
    response = client.get(
        path("list_trips"), params={"when": "past", "status": "pending"}
    )
    assert response.status_code == 200
    query = listing.await_args_list[0].args[2]
    assert (query.when, query.status) == (TripWhen.PAST, MemberStatus.PENDING)


def test_membership_routes_are_documented_with_the_409() -> None:
    paths = create_app().openapi()["paths"]
    leave = paths["/api/v1/trips/{trip_id}/membership/leave"]["post"]
    assert leave["responses"]["409"]["description"].startswith("The host cannot")
    assert leave["x-required-permission"] == "trips.members:WRITE"
    confirm = paths["/api/v1/trips/{trip_id}/membership/confirm"]["post"]
    assert confirm["x-required-permission"] == "trips.members:WRITE"
