"""Plan service pieces without a database: read model, input hash and endpoints."""

import uuid
from collections.abc import Iterator
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference, solo
from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.plan_group import plan_group
from tuttitrip.planning.plans.logic.input_builder import (
    NO_BUDGET,
    PlanInputError,
    input_hash,
    trip_days,
)
from tuttitrip.planning.plans.logic.read_model import build_content
from tuttitrip.planning.plans.logic.sample_plan import sample_plan
from tuttitrip.planning.plans.schemas import (
    ConflictCode,
    PlanCreate,
    PlanParams,
    PlanRead,
    WeightPreset,
)
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

BOB = AuthenticatedUser(sub="auth0|bob")
TRIP = uuid.UUID("00000000-0000-4000-8000-000000000000")


def as_read(content: dict[str, object], digest: str) -> PlanRead:
    return PlanRead.model_validate(
        {
            "id": uuid.uuid4(),
            "trip_id": TRIP,
            "version": 1,
            "input_hash": "0" * 64,
            "plan_hash": digest,
            "created_at": "2026-10-04T10:00:00Z",
            "params": PlanParams(alpha=1.0, weight_preset=WeightPreset.DEFAULT),
            **content,
        }
    )


# --- the read model ------------------------------------------------------------------


def test_group_plan_maps_onto_a_valid_plan_read() -> None:
    data = planning_input(reference(), lodging=False)
    group = plan_group(data)
    names = {p.id: f"Osoba {i}" for i, p in enumerate(data.people)}
    plan = as_read(build_content(data, group, names), group.plan.plan_hash)
    assert plan.fairness.group_size == 4
    assert plan.fairness.min_r == pytest.approx(group.min_r)
    assert plan.fairness.jain == pytest.approx(group.jain)
    assert plan.telemetry.solo_runs == 4
    assert plan.telemetry.solver == "local-search-v1"
    assert plan.budget.cost == group.plan.cost.total
    assert plan.budget.b_max is not None
    assert plan.budget.cost <= plan.budget.b_max
    assert plan.lodging is None
    assert plan.verdicts is None
    for person in plan.fairness.per_person:
        assert len(person.domains) == 5
        lodging = next(d for d in person.domains if d.domain.value == "lodging")
        assert lodging.not_applicable
        assert person.u <= person.u_star + 1e-6
        assert person.name.startswith("Osoba")
    stops = [i for d in plan.days for i in d.items]
    assert [s.place_id for s in stops] == list(group.plan.place_ids)
    assert {e.place_id for e in plan.explain} == {s.place_id for s in stops}
    assert len(plan.explain) == len(stops) * 4


def test_money_and_price_provenance_are_copied_into_the_result() -> None:
    data = planning_input(reference(), lodging=False)
    group = plan_group(data)
    plan = as_read(build_content(data, group, {}), group.plan.plan_hash)
    stops = [i for d in plan.days for i in d.items]
    unverified = [s for s in stops if not s.price_verified and s.price_base]
    assert unverified
    for stop in unverified:
        assert stop.price_base is not None
        assert stop.price_inflated is not None
        assert stop.price_inflated >= stop.price_base
    verified = [s for s in stops if s.price_verified]
    assert all(s.price_inflated == s.price_base for s in verified)


def test_one_person_has_r_one_and_jain_one() -> None:
    data = planning_input(solo(), lodging=False)
    group = plan_group(data)
    plan = as_read(build_content(data, group, {}), group.plan.plan_hash)
    assert plan.fairness.group_size == 1
    assert plan.fairness.jain == pytest.approx(1)
    (person,) = plan.fairness.per_person
    assert person.r == pytest.approx(1)
    assert plan.telemetry.solo_runs == 0


def test_unpriced_places_become_unknown_price_conflicts() -> None:
    data = planning_input(reference(), lodging=False)
    unpriced = next(p for p in data.places if not p.prices)
    forced = data.model_copy(update={"must": frozenset({unpriced.id})})
    group = plan_group(forced)
    plan = as_read(build_content(forced, group, {}), group.plan.plan_hash)
    codes = {(c.reason_code, c.place_id) for c in plan.conflicts}
    assert (ConflictCode.UNKNOWN_PRICE, unpriced.id) in codes
    stop = next(i for d in plan.days for i in d.items if i.place_id == unpriced.id)
    assert stop.cost_per_person is None


# --- the input hash ----------------------------------------------------------------


def test_input_hash_is_stable_and_sensitive() -> None:
    data = planning_input(reference(), lodging=False)
    base = input_hash(data, 1.0, "default", DEFAULT_PARAMS)
    assert base == input_hash(data, 1.0, "default", DEFAULT_PARAMS)
    assert len(base) == 64
    assert base != input_hash(data, 2.0, "default", DEFAULT_PARAMS)
    assert base != input_hash(data, 1.0, "equal", DEFAULT_PARAMS)
    assert base != input_hash(data, 1.0, "default", AlgorithmParams(vote_weight=0.5))
    vetoed = data.people[0].model_copy(
        update={"vetoes": data.people[0].vetoes | {data.places[0].id}}
    )
    changed = data.model_copy(update={"people": (vetoed, *data.people[1:])})
    assert base != input_hash(changed, 1.0, "default", DEFAULT_PARAMS)


def test_input_hash_ignores_set_order_and_input_order() -> None:
    data = planning_input(reference(), lodging=False)
    base = input_hash(data, 1.0, "default", DEFAULT_PARAMS)
    # Places and people are sorted by the builder; the hash must not care how
    # the sets inside were filled.
    first, second = sorted(p.id for p in data.places)[:2]
    one = data.model_copy(update={"must": frozenset({first, second})})
    other = data.model_copy(update={"must": frozenset({second, first})})
    assert input_hash(one, 1.0, "default", DEFAULT_PARAMS) == input_hash(
        other, 1.0, "default", DEFAULT_PARAMS
    )
    assert base != input_hash(one, 1.0, "default", DEFAULT_PARAMS)


def test_trip_days_are_inclusive() -> None:
    from datetime import date  # ruff: ignore[import-outside-top-level]

    assert trip_days(date(2026, 10, 9), date(2026, 10, 11)) == (
        date(2026, 10, 9),
        date(2026, 10, 10),
        date(2026, 10, 11),
    )
    assert len(trip_days(date(2026, 10, 9), date(2026, 10, 9))) == 1


# --- endpoints ----------------------------------------------------------------


def _client(grants: tuple[Grant, ...] | None = None) -> TestClient:
    app = create_app()
    if grants is None:
        authorize(app, BOB)
    else:
        authorize(app, BOB, grants)
    app.dependency_overrides[get_session] = lambda: None

    return TestClient(app)


@pytest.fixture
def member(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(
        trip_service,
        "get_membership",
        AsyncMock(
            return_value=TripMembership(trip_id=TRIP, sub=BOB.sub, role=TripRole.MEMBER)
        ),
    )
    with _client() as client:
        yield client


def test_a_plain_member_may_ask_for_a_plan(
    member: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        plan_service,
        "generate_plan",
        AsyncMock(return_value=(sample_plan(TRIP), True)),
    )
    assert member.post(path("create_plan", trip_id=TRIP)).status_code == 201


def test_alpha_is_optional_in_the_request() -> None:
    assert PlanCreate().alpha is None
    assert PlanCreate(alpha=0).alpha == 0
    with pytest.raises(ValueError, match="less than or equal"):
        PlanCreate(alpha=3.5)


def test_a_member_may_read_the_latest_plan(
    member: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        plan_service, "latest_plan", AsyncMock(return_value=sample_plan(TRIP))
    )
    assert member.get(path("get_latest_plan", trip_id=TRIP)).status_code == 200


def test_existing_version_is_200_and_a_new_one_201(
    member: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = sample_plan(TRIP)
    monkeypatch.setattr(
        trip_service,
        "get_membership",
        AsyncMock(
            return_value=TripMembership(trip_id=TRIP, sub=BOB.sub, role=TripRole.HOST)
        ),
    )
    monkeypatch.setattr(
        plan_service, "generate_plan", AsyncMock(return_value=(plan, True))
    )
    assert member.post(path("create_plan", trip_id=TRIP)).status_code == 201
    monkeypatch.setattr(
        plan_service, "generate_plan", AsyncMock(return_value=(plan, False))
    )
    assert member.post(path("create_plan", trip_id=TRIP)).status_code == 200


def test_a_trip_that_cannot_be_planned_is_422(
    member: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        trip_service,
        "get_membership",
        AsyncMock(
            return_value=TripMembership(trip_id=TRIP, sub=BOB.sub, role=TripRole.HOST)
        ),
    )
    error = PlanInputError("The trip needs start and end dates to plan")
    monkeypatch.setattr(plan_service, "generate_plan", AsyncMock(side_effect=error))
    response = member.post(path("create_plan", trip_id=TRIP))
    assert response.status_code == 422
    assert "dates" in response.json()["detail"]


def test_no_plan_yet_is_404_and_read_permission_is_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        trip_service,
        "get_membership",
        AsyncMock(
            return_value=TripMembership(trip_id=TRIP, sub=BOB.sub, role=TripRole.MEMBER)
        ),
    )
    monkeypatch.setattr(
        plan_service,
        "latest_plan",
        AsyncMock(side_effect=plan_service.PlanNotFoundError("x")),
    )
    with _client((Grant(Feature.PLANNING_PLANS, Access.READ),)) as c:
        assert c.get(path("get_latest_plan", trip_id=TRIP)).status_code == 404
        response = c.post(path("create_plan", trip_id=TRIP))
        assert response.status_code == 403
        assert response.json()["detail"] == "Missing permission planning.plans:WRITE"


def test_a_trip_without_a_budget_hides_the_sentinel() -> None:
    data = planning_input(reference(), lodging=False)
    trip = data.trip.model_copy(
        update={"budget_to": NO_BUDGET, "budget_from": Decimal(0)}
    )
    unlimited = data.model_copy(update={"trip": trip})
    group = plan_group(unlimited)
    plan = as_read(build_content(unlimited, group, {}), group.plan.plan_hash)
    assert plan.budget.unlimited
    assert plan.budget.b_to is None
    assert plan.budget.b_max is None
    assert plan.budget.over_budget == 0
    for person in plan.fairness.per_person:
        cost = next(d for d in person.domains if d.domain.value == "cost")
        assert cost.not_applicable


def test_own_place_misses_name_the_day_and_floors_below_the_wish_are_reported() -> None:
    data = planning_input(solo(), lodging=False)
    zoo = next(p for p in data.places if p.name == "Ogród zoologiczny")
    forced = data.model_copy(
        update={
            "places": (zoo,),
            "must": frozenset({zoo.id}),
            "trip": data.trip.model_copy(update={"days": data.trip.days[:1]}),
        }
    )
    plan_one = plan_group(forced)
    plan = as_read(build_content(forced, plan_one, {}), plan_one.plan.plan_hash)
    days = [m.day for m in plan.floors_missed if m.kind.value == "own_place_day"]
    assert days == [1]
    data = planning_input(reference(), lodging=False)
    group = plan_group(data)
    plan = as_read(build_content(data, group, {}), group.plan.plan_hash)
    unreachable = [
        c for c in plan.conflicts if c.reason_code is ConflictCode.FLOOR_UNREACHABLE
    ]
    reduced = [r.person_id for r in group.people if r.floor_eff < r.floor]
    assert [c.profile_ids for c in unreachable] == [[pid] for pid in reduced]


def test_a_rejected_must_names_who_vetoed_it() -> None:
    data = planning_input(reference(), lodging=False)
    vetoed = next(iter(data.people[3].vetoes))
    forced = data.model_copy(update={"must": frozenset({vetoed})})
    group = plan_group(forced)
    plan = as_read(build_content(forced, group, {}), group.plan.plan_hash)
    conflict = next(
        c for c in plan.conflicts if c.reason_code is ConflictCode.VETO_BLOCKS_PLACE
    )
    assert conflict.place_id == vetoed
    assert conflict.profile_ids == [data.people[3].id]
