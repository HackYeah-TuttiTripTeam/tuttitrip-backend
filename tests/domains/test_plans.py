"""Plan contract: fixed response, shape of section 10, trip and feature guards."""

import uuid
from collections.abc import Iterator
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.planning.plans.logic.hashing import compute_plan_hash
from tuttitrip.planning.plans.logic.metrics import jain_index
from tuttitrip.planning.plans.logic.sample_plan import (
    Scenario,
    sample_plan,
    variant_for,
)
from tuttitrip.planning.plans.schemas import (
    BudgetZone,
    PlanBudget,
    PlanCreate,
    PlanDomainScore,
    PlanRead,
    PlanStop,
)
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError

BOB = AuthenticatedUser(sub="auth0|bob")
TRIP = uuid.UUID("00000000-0000-4000-8000-000000000000")  # variant: group
TRIP_SOLO = uuid.UUID("00000000-0000-4000-8000-00000000000c")
TRIP_APPROVAL = uuid.UUID("00000000-0000-4000-8000-000000000001")


def _client(grants: tuple[Grant, ...] | None = None) -> TestClient:
    app = create_app()
    if grants is None:
        authorize(app, BOB)
    else:
        authorize(app, BOB, grants)
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    membership = TripMembership(trip_id=TRIP, sub=BOB.sub, role=TripRole.MEMBER)
    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(return_value=membership)
    )
    with _client() as test_client:
        yield test_client


def test_post_twice_gives_the_same_12_char_plan_hash(client: TestClient) -> None:
    first = client.post(path("create_plan", trip_id=TRIP))
    second = client.post(path("create_plan", trip_id=TRIP))
    assert first.status_code == 201
    assert len(first.json()["plan_hash"]) == 12
    assert first.json() == second.json()


def test_latest_matches_the_created_plan(client: TestClient) -> None:
    created = client.post(path("create_plan", trip_id=TRIP)).json()
    latest = client.get(path("get_latest_plan", trip_id=TRIP))
    assert latest.status_code == 200
    assert latest.json() == created


def test_plan_hash_changes_with_alpha(client: TestClient) -> None:
    base = client.post(path("create_plan", trip_id=TRIP)).json()
    other = client.post(path("create_plan", trip_id=TRIP), json={"alpha": 2}).json()
    assert other["params"]["alpha"] == 2
    assert other["plan_hash"] != base["plan_hash"]


def test_plan_has_the_fields_of_section_10() -> None:
    plan = sample_plan(TRIP)
    PlanRead.model_validate(plan.model_dump())
    assert plan.fairness.group_size == len(plan.fairness.per_person) == 4
    assert plan.fairness.min_r == min(p.r for p in plan.fairness.per_person)
    assert plan.fairness.jain == pytest.approx(0.998, abs=0.001)
    assert plan.fairness.min_r == pytest.approx(0.87, abs=0.005)
    assert plan.budget.cost == Decimal(1475)
    assert plan.budget.needs_approval is False
    assert plan.budget.kappa is None
    assert plan.floors_missed == []
    assert plan.verdicts is None
    for person in plan.fairness.per_person:
        assert len(person.domains) == 5
        assert person.floor_eff <= person.floor
    assert any(d.not_applicable for p in plan.fairness.per_person for d in p.domains)
    assert plan.conflicts
    assert all(c.reason_code for c in plan.conflicts)


def test_unverified_price_is_inflated_by_delta() -> None:
    items = [i for d in sample_plan(TRIP).days for i in d.items]
    unverified = [i for i in items if not i.price_verified]
    assert unverified
    for item in unverified:
        assert item.price_base is not None
        assert item.price_inflated == (item.price_base * Decimal("1.15")).quantize(
            Decimal("0.01")
        )
    assert all(i.price_inflated == i.price_base for i in items if i.price_verified)


def test_jain_of_one_person_is_one() -> None:
    assert jain_index([0.4]) == pytest.approx(1.0)
    assert jain_index([0.5, 0.5]) == pytest.approx(1.0)
    assert jain_index([1.0, 0.0]) == pytest.approx(0.5)


def test_openapi_exposes_the_plan_type() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    assert {
        "plan_hash",
        "fairness",
        "floors_missed",
        "budget",
        "explain",
        "verdicts",
    } <= set(schemas["PlanRead"]["properties"])
    assert {"r", "u_star", "domains", "floor_eff"} <= set(
        schemas["PersonFairness"]["properties"]
    )
    assert {"needs_approval", "kappa", "currency"} <= set(
        schemas["PlanBudget"]["properties"]
    )


def test_no_plan_schema_collides_with_another_domain() -> None:
    names = create_app().openapi()["components"]["schemas"]
    assert not [n for n in names if n.endswith(("-Input", "-Output"))]
    assert "PlanStop" in names
    assert "PlanItem" not in names or "PlanStop" in names


def test_money_is_serialised_as_a_string(client: TestClient) -> None:
    body = client.post(path("create_plan", trip_id=TRIP)).json()
    assert body["budget"]["cost"] == "1475.00"
    stop = body["days"][2]["items"][2]
    assert stop["price_inflated"] == "57.50"
    assert stop["price_source_url"] is None
    assert body["lodging"]["cost_total"] == "385.00"


def test_stub_marker_is_in_openapi() -> None:
    paths = create_app().openapi()["paths"]
    for route, method in (
        ("/api/v1/trips/{trip_id}/plans", "post"),
        ("/api/v1/trips/{trip_id}/plans/latest", "get"),
    ):
        operation = paths[route][method]
        assert operation["x-stub"] is True
        assert operation["summary"].endswith("(STUB)")
        assert "Args:" not in operation["description"]
        assert "Returns:" not in operation["description"]
    schemas = create_app().openapi()["components"]["schemas"]
    assert "STUB" in schemas["PlanRead"]["description"]
    assert "stub" in schemas["PlanTelemetry"]["properties"]["solver"]["description"]
    assert "404" in paths["/api/v1/trips/{trip_id}/plans/latest"]["get"]["responses"]


def test_solo_sample_has_one_person_and_no_conflicts() -> None:
    plan = sample_plan(TRIP, scenario=Scenario.SOLO)
    assert plan.fairness.group_size == 1
    assert plan.fairness.jain == pytest.approx(1.0)
    assert plan.fairness.min_r == pytest.approx(1.0)
    assert plan.conflicts == []


def test_approval_sample_matches_section_7() -> None:
    budget = sample_plan(TRIP, scenario=Scenario.APPROVAL).budget
    assert budget.cost == Decimal("1198.00")
    assert budget.over_budget == Decimal("98.00")
    assert budget.needs_approval
    assert budget.kappa == Decimal("18.70")


def test_kappa_is_set_exactly_when_approval_is_needed() -> None:
    money = Decimal(100)
    common = {
        "currency": "EUR",
        "cost": money,
        "b_from": money,
        "b_to": money,
        "b_max": money,
        "zone": "up_to_b_to",
        "over_budget": Decimal(0),
    }
    PlanBudget.model_validate({**common, "needs_approval": False})
    with pytest.raises(ValidationError):
        PlanBudget.model_validate({**common, "needs_approval": True})
    with pytest.raises(ValidationError):
        PlanBudget.model_validate(
            {**common, "needs_approval": False, "kappa": Decimal(5)}
        )


def test_q_is_null_exactly_when_not_applicable() -> None:
    PlanDomainScore(domain="food", q=None, not_applicable=True)
    with pytest.raises(ValidationError):
        PlanDomainScore(domain="food", q=None)
    with pytest.raises(ValidationError):
        PlanDomainScore(domain="food", q=50, not_applicable=True)


def test_plan_hash_helper_is_12_chars_and_stable() -> None:
    assert compute_plan_hash({"a": 1}) == compute_plan_hash({"a": 1})
    assert len(compute_plan_hash({"a": 1})) == 12


def test_non_member_gets_404(monkeypatch: pytest.MonkeyPatch) -> None:
    error = TripNotFoundError(str(TRIP))
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=error))
    with _client() as client:
        assert client.get(path("get_latest_plan", trip_id=TRIP)).status_code == 404
        assert client.post(path("create_plan", trip_id=TRIP)).status_code == 404


def test_post_without_write_permission_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    membership = TripMembership(trip_id=TRIP, sub=BOB.sub, role=TripRole.MEMBER)
    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(return_value=membership)
    )
    with _client((Grant(Feature.PLANNING_PLANS, Access.READ),)) as client:
        response = client.post(path("create_plan", trip_id=TRIP))
        assert response.status_code == 403
        assert response.json()["detail"] == "Missing permission planning.plans:WRITE"
        assert client.get(path("get_latest_plan", trip_id=TRIP)).status_code == 200


def _stops(plan: PlanRead) -> list[PlanStop]:
    return [i for d in plan.days for i in d.items]


def test_variant_is_chosen_from_the_trip() -> None:
    assert variant_for(TRIP) is Scenario.GROUP
    assert variant_for(TRIP_SOLO) is Scenario.SOLO
    assert variant_for(TRIP_APPROVAL) is Scenario.APPROVAL
    assert sample_plan(TRIP_SOLO).fairness.group_size == 1
    assert sample_plan(TRIP_APPROVAL).budget.needs_approval


def test_same_trip_gives_the_same_plan_hash() -> None:
    for trip in (TRIP, TRIP_SOLO, TRIP_APPROVAL):
        first, second = sample_plan(trip), sample_plan(trip, PlanCreate())
        assert first.plan_hash == second.plan_hash
        assert first.input_hash == second.input_hash
    hashes = {sample_plan(t).plan_hash for t in (TRIP, TRIP_SOLO, TRIP_APPROVAL)}
    assert len(hashes) == 3


@pytest.mark.parametrize("trip", [TRIP, TRIP_SOLO, TRIP_APPROVAL])
def test_every_variant_passes_the_schema_validators(trip: uuid.UUID) -> None:
    plan = sample_plan(trip)
    assert PlanRead.model_validate_json(plan.model_dump_json()) == plan
    budget = plan.budget
    assert (budget.kappa is not None) == budget.needs_approval
    assert budget.needs_approval == (budget.over_budget > 0)
    assert (budget.zone is BudgetZone.IN_MARGIN) == (budget.cost > budget.b_to)
    assert budget.over_budget == max(Decimal(0), budget.cost - budget.b_to)
    assert budget.cost <= budget.b_max
    assert all(len(p.domains) == 5 for p in plan.fairness.per_person)
    assert plan.lodging is not None
    assert plan.lodging.nights >= 1


def test_default_variant_shows_the_uncertain_states() -> None:
    stops = _stops(sample_plan(TRIP))
    assert any(not i.price_verified and i.price_source_url is None for i in stops)
    assert any(not i.hours_verified and i.hours_source_url is None for i in stops)
    assert any(
        i.price_inflated == Decimal("0.00") and i.price_source_url for i in stops
    )
    costs = [i.transfer.cost for i in stops if i.transfer]
    assert None in costs
    assert any(c is not None for c in costs)


def test_every_variant_has_a_stop_without_hours_source() -> None:
    for trip in (TRIP, TRIP_SOLO, TRIP_APPROVAL):
        assert any(i.hours_source_url is None for i in _stops(sample_plan(trip)))


def test_approval_variant_is_the_openapi_example() -> None:
    example = plan_service.openapi_examples()["needs_approval"]["value"]
    assert isinstance(example, dict)
    assert example["budget"]["needs_approval"] is True
    assert example["budget"]["kappa"] == "18.70"


def test_selection_rule_is_documented_in_the_endpoints() -> None:
    paths = create_app().openapi()["paths"]
    for route, method in (
        ("/api/v1/trips/{trip_id}/plans", "post"),
        ("/api/v1/trips/{trip_id}/plans/latest", "get"),
    ):
        assert (
            "sha256(str(trip_id).encode())[0] % 3"
            in paths[route][method]["description"]
        )


def test_approval_budget_follows_e6() -> None:
    budget = sample_plan(TRIP_APPROVAL).budget
    assert budget.strict_cost is not None
    assert budget.gain_points is not None
    assert budget.strict_cost <= budget.b_to
    assert budget.gain_points >= 8
    assert budget.kappa == (
        (budget.cost - budget.strict_cost) / Decimal(str(budget.gain_points))
    ).quantize(Decimal("0.01"))
    assert budget.zone is BudgetZone.IN_MARGIN


def test_approval_variant_over_http(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    membership = TripMembership(
        trip_id=TRIP_APPROVAL, sub=BOB.sub, role=TripRole.MEMBER
    )
    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(return_value=membership)
    )
    created = client.post(path("create_plan", trip_id=TRIP_APPROVAL))
    latest = client.get(path("get_latest_plan", trip_id=TRIP_APPROVAL))
    assert created.status_code == 201
    assert latest.status_code == 200
    body = latest.json()
    assert body["budget"]["needs_approval"] is True
    assert body["budget"]["kappa"] is not None
    assert body["plan_hash"] == created.json()["plan_hash"]


def test_every_sample_stop_has_an_address_and_the_schema_allows_null() -> None:
    for trip in (TRIP, TRIP_SOLO, TRIP_APPROVAL):
        assert all(stop.address for stop in _stops(sample_plan(trip)))
    schema = create_app().openapi()["components"]["schemas"]["PlanStop"]
    assert "address" in schema["properties"]
    assert "address" not in schema.get("required", [])


def test_address_is_in_the_plan_response(client: TestClient) -> None:
    stop = client.post(path("create_plan", trip_id=TRIP)).json()["days"][0]["items"][0]
    assert isinstance(stop["address"], str)
