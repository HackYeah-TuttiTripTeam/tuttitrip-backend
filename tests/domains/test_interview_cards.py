"""The interview shows cards (backend#220): fixed by the server, with a reminder."""

import asyncio
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from tests.shared.interview_world import World
from tuttitrip.interview.schemas import CardKind, QuestionField
from tuttitrip.interview.services import interview_agent as agent_module
from tuttitrip.interview.services.interview_agent import (
    interview_agent,
    waiting_for_the_host,
)
from tuttitrip.interview.services.interview_deps import InterviewDeps
from tuttitrip.shared.config.settings import get_settings

pytestmark = pytest.mark.filterwarnings(
    "ignore::pydantic_ai_harness.spend.UnpricedModelWarning"
)


def call(tool: str, /, **args: Any) -> ToolCallPart:  # ruff: ignore[any-type]
    return ToolCallPart(tool_name=tool, args=args)


class Script:
    """A model that makes one move per request and remembers what it was sent."""

    def __init__(self, *moves: ToolCallPart | str) -> None:
        self._moves = iter(moves)
        self.requests: list[list[ModelMessage]] = []

    def model(self) -> FunctionModel:
        def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            self.requests.append(list(messages))
            move = next(self._moves)
            return ModelResponse(
                parts=[TextPart(move)] if isinstance(move, str) else [move]
            )

        return FunctionModel(respond)


def run(world: World, script: Script) -> InterviewDeps:
    deps = world.deps()

    async def go() -> None:
        with interview_agent.override(model=script.model()):
            await interview_agent.run("Cześć", deps=deps)

    asyncio.run(go())
    return deps


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World()
    w.install(monkeypatch)
    asyncio.run(w.add_host())
    return w


@pytest.fixture
def nudging(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reminder on (the suite switches it off for the scripted models)."""
    settings = get_settings()
    interview = settings.interview.model_copy(update={"card_nudges": 1})
    monkeypatch.setattr(
        agent_module,
        "get_settings",
        lambda: settings.model_copy(update={"interview": interview}),
    )


# --- the server fixes the card ---------------------------------------------------


@pytest.mark.parametrize(
    ("field", "invented", "kind", "options"),
    [
        (QuestionField.DESTINATION, ["Gdańsk", "Kraków"], CardKind.CITY, []),
        (QuestionField.DATES, ["Sobota", "Niedziela"], CardKind.DATE_RANGE, []),
        (QuestionField.PEOPLE, None, CardKind.FAMILY_BUILDER, []),
        (QuestionField.PACE, None, CardKind.SLIDER, []),
        (QuestionField.BUDGET, ["EUR"], CardKind.BUDGET_RANGE, ["EUR"]),
    ],
)
def test_the_card_of_a_field_comes_from_the_server_not_from_the_model(
    world: World,
    field: QuestionField,
    invented: list[str] | None,
    kind: CardKind,
    options: list[str],
) -> None:
    deps = run(
        world,
        Script(
            call(
                "show_card",
                kind="choice",
                question="Pytanie?",
                options=invented,
                field=field.value,
            ),
            "Gotowe.",
        ),
    )
    card = deps.state.card
    assert card is not None
    assert (card.kind, card.options, card.field) == (kind, options, field)
    assert card.question == "Pytanie?"


def test_a_choice_of_a_field_gets_the_servers_options(world: World) -> None:
    deps = run(
        world,
        Script(
            call("show_card", kind="slider", question="Dieta?", field="diet"),
            "Gotowe.",
        ),
    )
    card = deps.state.card
    assert card is not None
    assert card.kind is CardKind.CHOICE
    assert card.options  # the diet tags, not an empty card


def test_a_confirmation_without_a_field_keeps_the_models_options(
    world: World,
) -> None:
    deps = run(
        world,
        Script(
            call(
                "show_card",
                kind="confirm",
                question="Zbudować plan?",
                options=["Tak", "Jeszcze nie"],
            ),
            "Gotowe.",
        ),
    )
    card = deps.state.card
    assert card is not None
    assert (card.kind, card.options) == (CardKind.CONFIRM, ["Tak", "Jeszcze nie"])


# --- the reminder ----------------------------------------------------------------


@pytest.mark.usefixtures("nudging")
def test_a_question_typed_without_a_card_is_sent_back_once_for_the_card(
    world: World,
) -> None:
    script = Script(
        "Dokąd jedziecie?",
        call(
            "show_card", kind="choice", question="Dokąd jedziecie?", field="destination"
        ),
        "Wybierz miasto.",
    )
    deps = run(world, script)
    assert deps.nudges == 1
    assert len(script.requests) == 3
    reminder = [
        part.content
        for message in script.requests[1]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if not isinstance(part, UserPromptPart) and hasattr(part, "content")
    ]
    assert any(
        "show_card" in str(content) and "destination" in str(content)
        for content in reminder
    )
    assert deps.state.card is not None
    assert deps.state.card.kind is CardKind.CITY


@pytest.mark.usefixtures("nudging")
def test_a_turn_that_showed_a_card_is_not_reminded(world: World) -> None:
    script = Script(
        call("show_card", kind="city", question="Dokąd?", field="destination"),
        "Wybierz miasto.",
    )
    deps = run(world, script)
    assert (deps.nudges, len(script.requests)) == (0, 2)


@pytest.mark.usefixtures("nudging")
def test_the_reminder_is_given_once_and_then_the_answer_stands(world: World) -> None:
    script = Script("Dokąd?", "No dokąd?")
    deps = run(world, script)
    assert (deps.nudges, len(script.requests)) == (1, 2)
    assert deps.state.card is None


def test_the_reminder_can_be_switched_off(world: World) -> None:
    script = Script("Dokąd?")
    deps = run(world, script)
    assert (deps.nudges, len(script.requests)) == (0, 1)


def _history(*parts: Any) -> list[ModelMessage]:  # ruff: ignore[any-type]
    return [ModelRequest(parts=list(parts))]


def test_a_refusal_of_this_turn_means_the_host_is_deciding() -> None:
    refused = ToolReturnPart(
        tool_name="set_trip_basics",
        content="NOT SAVED: the host set it",
        tool_call_id="1",
    )
    saved = ToolReturnPart(tool_name="add_person", content="{}", tool_call_id="2")
    assert waiting_for_the_host(_history(UserPromptPart("a"), refused))
    assert not waiting_for_the_host(_history(UserPromptPart("a"), saved))
    assert not waiting_for_the_host(_history(refused, UserPromptPart("tak")))
    assert not waiting_for_the_host([])
