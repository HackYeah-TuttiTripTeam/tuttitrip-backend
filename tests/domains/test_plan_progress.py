"""Progress of a plan computation: the sequence of stages and its API."""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import all_scenarios, reference
from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.planning.logic.budget_consent import CONSENT_PLANS, plan_with_consent
from tuttitrip.planning.logic.plan_group import plan_group
from tuttitrip.planning.logic.progress import (
    PLAN_STEPS,
    PlanProgress,
    PlanStep,
)
from tuttitrip.planning.plans.schemas import PlanProgressRead
from tuttitrip.planning.plans.services import plan_progress, plan_service
from tuttitrip.planning.schemas import LodgingStay
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

STAY = LodgingStay(nights=2, price_per_night=Decimal(250))
TRIP = uuid.UUID("00000000-0000-4000-8000-000000000000")
BOB = AuthenticatedUser(sub="auth0|bob")


def test_steps_are_in_the_order_of_the_algorithm() -> None:
    assert [s.value for s in PLAN_STEPS] == [
        "catalogue",
        "reference",
        "search",
        "floors",
        "budget",
        "verdicts",
    ]
    assert (
        PlanProgress(PlanStep.BUDGET).position == PLAN_STEPS.index(PlanStep.BUDGET) + 1
    )


def test_group_plan_reports_one_solo_run_per_person_then_search_and_floors() -> None:
    data = planning_input(reference())
    events: list[PlanProgress] = []
    plan_group(data, lodging=STAY, progress=events.append)
    people = len(data.people)
    assert events == [
        *(PlanProgress(PlanStep.REFERENCE, i, people) for i in range(1, people + 1)),
        PlanProgress(PlanStep.SEARCH),
        PlanProgress(PlanStep.FLOORS),
    ]


def test_one_person_has_no_solo_run() -> None:
    data = planning_input(all_scenarios()["solo"], lodging=False)
    events: list[PlanProgress] = []
    plan_group(data, progress=events.append)
    assert [e.step for e in events] == [PlanStep.SEARCH, PlanStep.FLOORS]


def test_consent_runs_are_the_budget_stage_and_never_go_back() -> None:
    data = planning_input(reference())
    trip = data.trip.model_copy(
        update={"budget_to": Decimal(900), "budget_from": Decimal(700)}
    )
    data = data.model_copy(update={"trip": trip})
    events: list[PlanProgress] = []
    decision = plan_with_consent(data, lodging=STAY, progress=events.append)
    assert decision.runs == CONSENT_PLANS + 1
    tail = events[-CONSENT_PLANS:]
    assert tail == [
        PlanProgress(PlanStep.BUDGET, i, CONSENT_PLANS)
        for i in range(1, CONSENT_PLANS + 1)
    ]
    positions = [e.position for e in events]
    assert positions == sorted(positions)


def test_progress_does_not_change_the_plan() -> None:
    data = planning_input(reference())
    quiet = plan_with_consent(data, lodging=STAY)
    seen: list[PlanProgress] = []
    loud = plan_with_consent(data, lodging=STAY, progress=seen.append)
    assert seen
    assert loud.chosen.plan.plan_hash == quiet.chosen.plan.plan_hash
    assert loud.needs_approval == quiet.needs_approval
    assert loud.kappa == quiet.kappa


def test_registry_holds_the_stage_only_while_tracking() -> None:
    trip = uuid.uuid4()
    assert plan_progress.current(trip) is None
    with plan_progress.track(trip) as sink:
        sink(PlanProgress(PlanStep.SEARCH))
        assert plan_progress.current(trip) == PlanProgress(PlanStep.SEARCH)
        sink(PlanProgress(PlanStep.REFERENCE, 2, 4))
        assert plan_progress.current(trip) == PlanProgress(PlanStep.REFERENCE, 2, 4)
    assert plan_progress.current(trip) is None


def test_registry_is_cleared_when_the_computation_fails() -> None:
    trip = uuid.uuid4()

    def fail() -> None:
        with plan_progress.track(trip) as sink:
            sink(PlanProgress(PlanStep.SEARCH))
            raise RuntimeError

    with pytest.raises(RuntimeError):
        fail()
    assert plan_progress.current(trip) is None


def test_progress_endpoint_reports_the_running_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    membership = TripMembership(trip_id=TRIP, sub=BOB.sub, role=TripRole.MEMBER)
    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(return_value=membership)
    )
    app = create_app()
    authorize(app, BOB)
    app.dependency_overrides[get_session] = lambda: None
    url = path("get_plan_progress", trip_id=TRIP)
    with TestClient(app) as client:
        assert client.get(url).json() is None
        with plan_progress.track(TRIP) as sink:
            sink(PlanProgress(PlanStep.REFERENCE, 2, 4))
            body = PlanProgressRead.model_validate(client.get(url).json())
        assert body == PlanProgressRead(
            step=PlanStep.REFERENCE,
            position=2,
            total=len(PLAN_STEPS),
            item=2,
            items=4,
        )
        assert plan_service.computation_progress(TRIP) is None
