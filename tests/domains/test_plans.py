"""Plan contract: fixed response, shape of section 10, trip and feature guards."""

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.planning.plans.logic.sample_plan import jain_index, sample_plan
from tuttitrip.planning.plans.schemas import PlanRead
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError

BOB = AuthenticatedUser(sub="auth0|bob")
TRIP = uuid.uuid4()


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
    assert plan.budget.cost == 1475
    assert plan.budget.needs_approval is False
    assert plan.budget.kappa is None
    assert plan.floors_missed == []
    assert plan.verdicts is None
    for person in plan.fairness.per_person:
        assert len(person.domains) == 5
        assert person.floor_eff <= person.floor
    assert any(d.not_applicable for p in plan.fairness.per_person for d in p.domains)
    assert plan.conflicts
    assert all(c.reason for c in plan.conflicts)


def test_unverified_price_is_inflated_by_delta() -> None:
    items = [i for d in sample_plan(TRIP).days for i in d.items]
    unverified = [i for i in items if not i.price_verified]
    assert unverified
    for item in unverified:
        assert item.price_inflated == pytest.approx(item.price_base * 1.15)
    assert all(i.price_inflated == i.price_base for i in items if i.price_verified)


def test_jain_of_one_person_is_one() -> None:
    assert jain_index([0.4]) == pytest.approx(1.0)
    assert jain_index([0.5, 0.5]) == pytest.approx(1.0)
    assert jain_index([1.0, 0.0]) == pytest.approx(0.5)


def test_openapi_exposes_the_plan_type() -> None:
    schema = create_app().openapi()["components"]["schemas"]["PlanRead"]["properties"]
    assert {
        "plan_hash",
        "fairness",
        "floors_missed",
        "budget",
        "explain",
        "verdicts",
    } <= set(schema)
    person = create_app().openapi()["components"]["schemas"]["PersonFairness"][
        "properties"
    ]
    assert {"r", "u_star", "domains", "floor_eff"} <= set(person)
    budget = create_app().openapi()["components"]["schemas"]["Budget"]["properties"]
    assert {"needs_approval", "kappa"} <= set(budget)


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
