"""Preliminary plan during the interview (#60): assumptions, service, tool, endpoint."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from tests.domains.test_interview import _person
from tests.domains.test_interview_questions import COMPLETE, known, two
from tests.shared.fakes import authorize
from tests.shared.interview_world import World
from tests.shared.paths import path
from tuttitrip.interview import constants
from tuttitrip.interview.logic import plan_defaults
from tuttitrip.interview.logic.plan_defaults import MissingCityError
from tuttitrip.interview.schemas import AssumptionCode
from tuttitrip.interview.services import draft_plan_service, question_service
from tuttitrip.interview.services.interview_agent import interview_agent
from tuttitrip.main import create_app
from tuttitrip.planning.plans.schemas import PlanAssumptions, PlanRead
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.plans.services.plan_service import PlanInputError
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

pytestmark = pytest.mark.filterwarnings(
    "ignore::pydantic_ai_harness.spend.UnpricedModelWarning"
)

WEDNESDAY = date(2026, 10, 7)
SATURDAY = date(2026, 10, 10)
CITY = {"city_slug": "krakow", "destination": "Kraków"}
ME = AuthenticatedUser(sub="auth0|host")


# --- the pure assumptions ----------------------------------------------------------


def test_a_trip_with_only_a_city_assumes_one_day_two_adults_and_the_rest() -> None:
    spec = plan_defaults.plan_defaults(known([_person("Host")], CITY), WEDNESDAY)
    assert spec.assumptions == PlanAssumptions(
        start_date=SATURDAY, end_date=SATURDAY, min_people=2
    )
    assert [n.code for n in spec.notes] == [
        AssumptionCode.DATES,
        AssumptionCode.PEOPLE,
        AssumptionCode.BUDGET,
        AssumptionCode.PREFERENCES,
    ]
    assert "dwoje dorosłych" in spec.notes[1].text
    assert "jeden dzień" in spec.notes[0].text
    assert "10.10.2026" in spec.notes[0].text


def test_the_assumed_day_is_the_next_saturday_not_today() -> None:
    on_saturday = plan_defaults.plan_defaults(known([_person("Host")], CITY), SATURDAY)
    assert on_saturday.assumptions.start_date == date(2026, 10, 17)


def test_data_the_host_gave_is_not_assumed() -> None:
    spec = plan_defaults.plan_defaults(
        known(two(), {**COMPLETE, **CITY}, filled_prefs=True), WEDNESDAY
    )
    assert spec.notes == []
    assert spec.assumptions.start_date is None
    assert spec.assumptions.end_date is None


def test_a_lone_start_date_means_one_day() -> None:
    spec = plan_defaults.plan_defaults(
        known([_person("Host")], {**CITY, "start_date": SATURDAY}), WEDNESDAY
    )
    assert spec.assumptions.end_date == SATURDAY
    assert AssumptionCode.DATES not in [n.code for n in spec.notes]


def test_the_same_knowledge_gives_the_same_assumptions() -> None:
    host = _person("Host")
    first = plan_defaults.plan_defaults(known([host], CITY), WEDNESDAY)
    again = plan_defaults.plan_defaults(known([host], CITY), WEDNESDAY)
    assert first == again


def test_without_a_city_there_is_nothing_to_plan() -> None:
    with pytest.raises(MissingCityError):
        plan_defaults.plan_defaults(known([_person("Host")]), WEDNESDAY)


# --- the service, the tool and the endpoint ----------------------------------------


def _plan_read(version: int = 1) -> PlanRead:
    return PlanRead.model_construct(
        id=uuid.uuid4(), version=version, plan_hash="a1b2c3d4e5f6"
    )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World()
    w.install(monkeypatch)
    asyncio.run(w.add_host())
    w.trip = w.trip.model_copy(update=CITY)
    # The instructions ask for the measured question; it is not under test here.
    monkeypatch.setattr(question_service, "choose", AsyncMock(return_value=None))
    return w


@pytest.fixture
def generate(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=(_plan_read(), True))
    monkeypatch.setattr(plan_service, "generate_plan", mock)
    return mock


def test_the_service_plans_with_the_assumptions_and_reports_them(
    world: World, generate: AsyncMock
) -> None:
    draft = asyncio.run(
        draft_plan_service.build(world.session, world.membership, WEDNESDAY)
    )
    assert generate.await_args is not None
    membership, data, assumptions = generate.await_args.args[1:]
    assert (membership, data) == (world.membership, None)
    assert assumptions.min_people == 2
    assert assumptions.start_date == SATURDAY
    assert draft.plan_hash == "a1b2c3d4e5f6"
    assert {a.code for a in draft.assumptions} >= {
        AssumptionCode.DATES,
        AssumptionCode.PEOPLE,
    }


def test_the_service_refuses_a_trip_without_a_city(
    world: World, generate: AsyncMock
) -> None:
    world.trip = world.trip.model_copy(update={"city_slug": None})
    with pytest.raises(MissingCityError):
        asyncio.run(draft_plan_service.build(world.session, world.membership))
    generate.assert_not_awaited()


def call(tool: str) -> ToolCallPart:
    return ToolCallPart(tool_name=tool, args={})


def run_tool(
    world: World, *moves: ToolCallPart | str
) -> tuple[Any, list[ModelMessage]]:
    queue = iter(moves)

    def respond(_m: list[ModelMessage], _i: AgentInfo) -> ModelResponse:
        move = next(queue)
        return ModelResponse(
            parts=[TextPart(move)] if isinstance(move, str) else [move]
        )

    deps = world.deps()

    async def go() -> list[ModelMessage]:
        with interview_agent.override(model=FunctionModel(respond)):
            result = await interview_agent.run("zbuduj plan", deps=deps)
        return result.all_messages()

    return deps, asyncio.run(go())


def returned(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]


def test_the_tool_builds_at_most_one_plan_per_turn(
    world: World, generate: AsyncMock
) -> None:
    deps, messages = run_tool(
        world, call("build_plan_now"), call("build_plan_now"), "Gotowe."
    )
    assert generate.await_count == 1
    first, second = returned(messages)
    assert first.content == second.content
    assert deps.state.draft_plan is not None
    assert deps.state.draft_plan.version == 1


def test_the_tool_asks_for_the_city_instead_of_building(
    world: World, generate: AsyncMock
) -> None:
    world.trip = world.trip.model_copy(update={"city_slug": None})
    deps, messages = run_tool(world, call("build_plan_now"), "Jakie miasto?")
    assert str(returned(messages)[0].content).startswith("NOT BUILT")
    assert deps.state.draft_plan is None
    generate.assert_not_awaited()


def test_the_tool_reports_data_that_cannot_be_planned(
    world: World, generate: AsyncMock
) -> None:
    generate.side_effect = PlanInputError("Unknown city 'x'")
    _deps, messages = run_tool(world, call("build_plan_now"), "Nie da się.")
    assert "Unknown city" in str(returned(messages)[0].content)


@pytest.fixture
def client(world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = create_app()
    authorize(app, ME)
    app.dependency_overrides[get_session] = lambda: world.session
    monkeypatch.setattr(
        trip_service,
        "get_membership",
        AsyncMock(
            return_value=TripMembership(
                trip_id=world.trip_id, sub=ME.sub, role=TripRole.CO_HOST
            )
        ),
    )
    monkeypatch.setattr(profile_service, "list_profiles", AsyncMock(return_value=[]))
    with TestClient(app) as test_client:
        yield test_client


def test_the_endpoint_returns_the_plan_and_the_assumptions(
    client: TestClient, world: World, generate: AsyncMock
) -> None:
    response = client.post(path("build_draft_plan", trip_id=world.trip_id))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version"] == 1
    assert body["plan_hash"] == "a1b2c3d4e5f6"
    assert [a["code"] for a in body["assumptions"]][:2] == ["dates", "people"]
    generate.assert_awaited_once()


def test_the_endpoint_says_to_give_the_city(
    client: TestClient, world: World, generate: AsyncMock
) -> None:
    world.trip = world.trip.model_copy(update={"city_slug": None})
    response = client.post(path("build_draft_plan", trip_id=world.trip_id))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "plan.missing_inputs"
    assert detail["message"] == constants.MISSING_CITY_PL == "Podaj miasto"
    assert detail["missing"] == [
        {"field": "destination", "person_id": None, "kind": "city", "options": []}
    ]
    generate.assert_not_awaited()


def test_the_endpoint_maps_unplannable_data_to_422(
    client: TestClient, world: World, generate: AsyncMock
) -> None:
    generate.side_effect = PlanInputError("Unknown city 'x'")
    response = client.post(path("build_draft_plan", trip_id=world.trip_id))
    assert response.status_code == 422
    assert "Unknown city" in response.json()["detail"]
