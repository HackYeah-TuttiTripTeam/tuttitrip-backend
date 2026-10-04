"""A trip member's interview about their own interests (#91)."""

import asyncio
import uuid
from collections.abc import Iterator
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
from pydantic_ai.models.test import TestModel

from tests.domains.test_interview import _row
from tests.domains.test_interview_questions import known
from tests.shared.fakes import authorize
from tests.shared.interview_world import World
from tests.shared.paths import path
from tuttitrip.interview import constants
from tuttitrip.interview.logic import next_question
from tuttitrip.interview.schemas import KnowledgeField, QuestionField, QuestionKey
from tuttitrip.interview.services import agui_service, run_guard, session_service
from tuttitrip.interview.services.interview_agent import interview_agent
from tuttitrip.interview.services.session_service import NoProfileError
from tuttitrip.main import create_app
from tuttitrip.places.schemas import PlaceTag
from tuttitrip.profiles.schemas import ProfileCreate
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripRoleError

pytestmark = pytest.mark.filterwarnings(
    "ignore::pydantic_ai_harness.spend.UnpricedModelWarning"
)

ME = AuthenticatedUser(sub="auth0|host")
HOST_TOOLS = {
    "set_trip_basics",
    "add_person",
    "update_person",
    "set_budget",
    "build_plan_now",
    "set_constraint",
    "set_diet",
    "classify_diet",
    "classify_constraint",
    "add_interest",
    "set_importance_points",
}
MEMBER_TOOLS = {
    "add_my_interest",
    "set_my_diet",
    "set_my_constraint",
    "set_my_importance_points",
    "classify_my_diet",
    "classify_my_constraint",
}


class Family:
    """A trip with a host, an organizer-made profile and the member's own profile."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.world = World(TripRole.MEMBER)
        self.world.install(monkeypatch)
        # The first profile is the member's own (an account profile).
        self.me = asyncio.run(self.world.add_host())
        self.other = asyncio.run(
            profile_service.create_profile(
                self.world.session,
                self.world.membership,
                ProfileCreate(display_name="Ania", age=34),
            )
        )
        monkeypatch.setattr(
            profile_service,
            "find_account_profile",
            AsyncMock(return_value=self.me.id),
        )

    def deps(self) -> Any:  # ruff: ignore[any-type]
        return self.world.deps(own_profile_id=self.me.id)


@pytest.fixture
def family(monkeypatch: pytest.MonkeyPatch) -> Family:
    return Family(monkeypatch)


def call(tool: str, /, **args: Any) -> ToolCallPart:  # ruff: ignore[any-type]
    return ToolCallPart(tool_name=tool, args=args)


def script(
    *moves: ToolCallPart | str, seen: list[AgentInfo] | None = None
) -> FunctionModel:
    queue = iter(moves)

    def respond(_m: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append(info)
        move = next(queue)
        return ModelResponse(
            parts=[TextPart(move)] if isinstance(move, str) else [move]
        )

    return FunctionModel(respond)


def run(deps: Any, model: Any, prompt: str = "x") -> list[ModelMessage]:  # ruff: ignore[any-type]
    async def go() -> list[ModelMessage]:
        with interview_agent.override(model=model):
            result = await interview_agent.run(prompt, deps=deps)
        return result.all_messages()

    return asyncio.run(go())


def returned(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]


# --- the tools by role ---


def test_a_member_is_offered_only_their_own_tools(family: Family) -> None:
    seen: list[AgentInfo] = []
    run(family.deps(), script("Cześć!", seen=seen))
    offered = {t.name for t in seen[0].function_tools}
    assert offered == MEMBER_TOOLS | {"show_card"}
    assert "organizer" in str(seen[0].instructions)
    for tool in seen[0].function_tools:
        if tool.name == "show_card":
            continue  # names whom a question is about; it writes nothing
        assert "person_id" not in tool.parameters_json_schema.get("properties", {})


def test_the_host_is_offered_the_trip_tools_and_none_of_the_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(TripRole.HOST)
    world.install(monkeypatch)
    asyncio.run(world.add_host())
    seen: list[AgentInfo] = []
    run(world.deps(), script("Cześć!", seen=seen))
    offered = {t.name for t in seen[0].function_tools}
    assert offered == HOST_TOOLS | {"show_card"}
    assert not offered & MEMBER_TOOLS


# --- what a member can say ---


def test_an_interest_goes_to_the_members_own_profile(family: Family) -> None:
    run(
        family.deps(),
        script(call("add_my_interest", interest="science"), "Zapisane."),
        "lubię muzea techniki",
    )
    saved = family.world.saved
    assert set(saved) == {family.me.id}
    assert saved[family.me.id].interests == {PlaceTag.SCIENCE: 1.0}


def test_a_test_model_writing_changes_only_the_members_profile(family: Family) -> None:
    run(family.deps(), TestModel(call_tools=["add_my_interest", "set_my_diet"]))
    assert set(family.world.saved) == {family.me.id}
    assert family.other.id not in family.world.saved


def test_asking_for_the_budget_reaches_no_tool(family: Family) -> None:
    messages = run(
        family.deps(),
        script(
            call("set_budget", currency="PLN", total_max="100"),
            "To ustawia organizator.",
        ),
        "ustaw budżet 100 zł",
    )
    retry = [
        p
        for m in messages
        for p in m.parts
        if p.part_kind == "retry-prompt" and p.tool_name == "set_budget"
    ]
    assert retry, "the unknown tool is refused, not run"
    assert family.world.writes == 2  # only the two profiles made by the fixture
    assert family.world.trip.budget_total_max is None
    assert not returned(messages)


def test_someone_elses_preferences_have_no_tool_either(family: Family) -> None:
    run(
        family.deps(),
        script(
            call("set_diet", person_id=str(family.other.id), diet="vegan"),
            "Tego nie zmienię.",
        ),
    )
    assert family.world.saved == {}


def test_a_members_values_are_not_marked_as_the_assistants(family: Family) -> None:
    run(
        family.deps(),
        script(call("set_my_constraint", kind="stairs"), "Zapisane."),
    )
    assert family.me.id in family.world.saved
    assert family.world.digests == {}  # they read as a person's, so the host is asked


def test_the_host_has_no_say_over_a_values_the_member_wrote_without_asking(
    family: Family,
) -> None:
    run(
        family.deps(),
        script(call("set_my_diet", diet="vegan"), "Zapisane."),
    )
    # A second write by the member is not blocked by a "host value" guard.
    run(
        family.deps(),
        script(call("set_my_diet", diet="vegetarian"), "Zapisane."),
    )
    diet = family.world.saved[family.me.id].diet
    assert {t.value for t in diet.tags} == {"vegan", "vegetarian"}


# --- the panel and the questions ---


def test_the_members_panel_has_only_themselves_and_no_budget(family: Family) -> None:
    family.world.trip = family.world.trip.model_copy(
        update={"budget_total_max": 5000, "currency": "PLN"}
    )
    view = asyncio.run(
        session_service.get_knowledge(family.world.session, family.world.membership)
    )
    assert [p.id for p in view.people] == [family.me.id]
    assert [p.profile_id for p in view.preferences] == [family.me.id]
    assert view.trip.budget_total_max is None
    assert view.trip.currency is None
    assert {m.field for m in view.missing} <= {KnowledgeField.PREFERENCES}


def test_the_member_is_asked_about_themselves_in_order(family: Family) -> None:
    panel = asyncio.run(
        session_service.get_knowledge(family.world.session, family.world.membership)
    )
    asked: set[QuestionKey] = set()
    order = []
    while question := next_question.member_question(panel, asked):
        assert question.person_id == family.me.id
        order.append(question.field)
        asked.add(QuestionKey(field=question.field, person_id=question.person_id))
    assert tuple(order) == constants.MEMBER_FIELDS
    assert order[0] is QuestionField.INTERESTS


def test_a_panel_without_people_asks_nothing() -> None:
    assert next_question.member_question(known([])) is None


def test_the_members_instructions_have_no_budget_and_no_plan_offer(
    family: Family,
) -> None:
    seen: list[AgentInfo] = []
    family.world.trip = family.world.trip.model_copy(
        update={"budget_total_max": 5000, "currency": "PLN"}
    )
    run(family.deps(), script("Cześć", seen=seen))
    text = str(seen[0].instructions)
    assert "5000" not in text
    assert "Zbuduj plan" not in text
    assert '"field":"interests"' in text


# --- whose session it is ---


def test_the_role_decides_whose_interview_it_is(family: Family) -> None:
    session = family.world.session
    assert (
        asyncio.run(session_service.owner_profile(session, family.world.membership))
        == family.me.id
    )
    host = TripMembership(
        trip_id=family.world.trip_id, sub="auth0|boss", role=TripRole.CO_HOST
    )
    assert asyncio.run(session_service.owner_profile(session, host)) is None


def test_a_member_without_a_profile_has_nobody_to_interview(
    family: Family, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        profile_service, "find_account_profile", AsyncMock(return_value=None)
    )
    with pytest.raises(NoProfileError):
        asyncio.run(
            session_service.owner_profile(family.world.session, family.world.membership)
        )


def test_a_member_cannot_name_the_hosts_session(family: Family) -> None:
    family.world.session_owner = None  # the trip's own session
    with pytest.raises(session_service.SessionNotFoundError):
        asyncio.run(
            session_service.check_owned(
                family.world.session, family.world.membership, uuid.uuid4()
            )
        )


def test_a_member_can_use_their_own_session_and_the_host_cannot(
    family: Family,
) -> None:
    family.world.session_owner = family.me.id
    thread = uuid.uuid4()
    assert (
        asyncio.run(
            session_service.check_owned(
                family.world.session, family.world.membership, thread
            )
        )
        == family.me.id
    )
    host = TripMembership(
        trip_id=family.world.trip_id, sub="auth0|boss", role=TripRole.HOST
    )
    with pytest.raises(session_service.SessionNotFoundError):
        asyncio.run(session_service.check_owned(family.world.session, host, thread))


def test_the_turn_of_a_member_runs_with_their_profile_in_the_deps(
    family: Family, monkeypatch: pytest.MonkeyPatch
) -> None:
    family.world.session_owner = family.me.id
    monkeypatch.setattr(session_service, "load_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        agui_service, "get_sessionmaker", lambda: family.world.deps().sessions
    )
    thread = uuid.uuid4()
    body = (
        b'{"threadId":"%s","runId":"r","state":{},"messages":[{"id":"m","role":"user",'
        b'"content":"lubie muzea"}],"tools":[],"context":[],"forwardedProps":{}}'
    ) % str(thread).encode()
    request = agui_service.read_request(body)
    stream = asyncio.run(
        agui_service.begin(request, None, family.world.membership, family.world.session)
    )
    asyncio.run(run_guard.release(stream.claim))
    # check_owned found the member's own session and handed their profile on
    assert session_service.load_history.await_count == 1  # ty: ignore[unresolved-attribute]


# --- the routes ---


@pytest.fixture
def client(family: Family, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = create_app()
    authorize(app, ME)
    app.dependency_overrides[get_session] = lambda: family.world.session

    def check(_s: object, _t: object, _sub: str, min_role: TripRole) -> TripMembership:
        if not family.world.membership.role.satisfies(min_role):
            msg = f"Trip role '{min_role}' required"
            raise TripRoleError(msg)
        return family.world.membership

    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=check))
    with TestClient(app) as test_client:
        yield test_client


def test_a_member_starts_their_own_session_and_the_draft_plan_stays_closed(
    client: TestClient, family: Family, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = AsyncMock()

    stored = _row()
    insert = AsyncMock(return_value=stored)
    monkeypatch.setattr("tuttitrip.interview.db.insert_open_session", insert)
    monkeypatch.setattr("tuttitrip.interview.db.select_open_session", row)
    started = client.post(path("start_session", trip_id=family.world.trip_id))
    assert started.status_code == 201, started.text
    assert insert.await_args is not None
    assert insert.await_args.args[3] == family.me.id  # the member's profile
    plan = client.post(path("build_draft_plan", trip_id=family.world.trip_id))
    assert plan.status_code == 403


def test_a_member_without_a_profile_gets_404(
    client: TestClient, family: Family, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        profile_service, "find_account_profile", AsyncMock(return_value=None)
    )
    response = client.post(path("start_session", trip_id=family.world.trip_id))
    assert response.status_code == 404
    assert "profile" in response.json()["detail"]
