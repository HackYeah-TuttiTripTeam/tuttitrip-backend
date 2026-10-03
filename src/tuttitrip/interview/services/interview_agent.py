"""Interview agent: asks one card at a time and fills the shared state."""

import os

from ag_ui.core import EventType, StateSnapshotEvent
from openai import AsyncOpenAI
from pydantic_ai import Agent, RunContext, ToolReturn
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.ui import StateDeps

from tuttitrip.interview.schemas import (
    Fact,
    InterviewState,
    ShownCard,
)

# Spike: same names the model catalog (backend#39) will read from Settings.
GB10_BASE_URL_ENV = "TUTTITRIP_LLM__GB10_BASE_URL"
GB10_KEY_ENV = "TUTTITRIP_LLM__GB10_API_KEY"
GB10_MODEL = "qwen3.8-27b-chat"

type InterviewDeps = StateDeps[InterviewState]


def _model() -> OpenAIChatModel:
    """Qwen on the GB10 through its OpenAI-compatible LiteLLM endpoint.

    Returns:
        The chat model (spike: configured by two environment variables).
    """
    client = AsyncOpenAI(
        base_url=os.environ.get(GB10_BASE_URL_ENV, "https://llm.gburek.app/v1"),
        api_key=os.environ.get(GB10_KEY_ENV, "unset"),
    )
    return OpenAIChatModel(GB10_MODEL, provider=OpenAIProvider(openai_client=client))


def _snapshot(state: InterviewState) -> ToolReturn:
    """Tool result that also pushes the whole state to the client.

    Args:
        state: Current shared state.

    Returns:
        A ``ToolReturn`` whose metadata carries a ``STATE_SNAPSHOT`` event.
    """
    return ToolReturn(
        return_value="ok",
        metadata=[
            StateSnapshotEvent(
                type=EventType.STATE_SNAPSHOT, snapshot=state.model_dump(mode="json")
            )
        ],
    )


interview_agent: Agent[InterviewDeps] = Agent(
    _model(),
    model_settings={"parallel_tool_calls": False},
    deps_type=StateDeps[InterviewState],
    instructions=(
        "Jesteś asystentem, który po polsku wypytuje organizatora wyjazdu. "
        "Pytaj o jedną rzecz naraz i wywołuj narzędzia pojedynczo. "
        "Gdy poznasz fakt, wywołaj remember. "
        "Pytanie zadawaj przez show_card (kind=choice z 2 do 4 opcjami), "
        "a po wywołaniu show_card napisz najwyżej jedno krótkie zdanie. "
        "Dopiero gdy znasz miejsce, liczbę dni, skład grupy i termin, "
        "wywołaj show_card z "
        "kind=confirm i opcjami Zatwierdź, Popraw. Użytkownik może zmienić "
        "fakty w panelu: aktualny stan jest poniżej, traktuj go jako prawdę."
    ),
)


@interview_agent.instructions
def current_state(ctx: RunContext[InterviewDeps]) -> str:
    """Tell the model what the panel currently contains.

    Args:
        ctx: Run context holding the AG-UI state.

    Returns:
        The facts as plain text.
    """
    facts = "; ".join(f"{f.label}={f.value}" for f in ctx.deps.state.facts)
    return f"Co już wiemy: {facts or 'nic'}."


@interview_agent.tool
def show_card(
    ctx: RunContext[InterviewDeps],
    kind: str,
    question: str,
    options: list[str],
) -> ToolReturn:
    """Show a card with a question in the client.

    Args:
        ctx: Run context holding the AG-UI state.
        kind: ``choice``, ``slider``, ``text`` or ``confirm``.
        question: The question, in Polish.
        options: Choices to offer (empty for ``text``).

    Returns:
        A state snapshot with the new card.
    """
    ctx.deps.state.card = ShownCard.model_validate(
        {"kind": kind, "question": question, "options": options}
    )
    return _snapshot(ctx.deps.state)


@interview_agent.tool
def remember(
    ctx: RunContext[InterviewDeps], key: str, label: str, value: str
) -> ToolReturn:
    """Store a fact in the "Co już wiem" panel (replaces the same key).

    Args:
        ctx: Run context holding the AG-UI state.
        key: Stable machine key, e.g. ``city``.
        label: Polish label for the panel.
        value: The value.

    Returns:
        A state snapshot with the updated facts.
    """
    state = ctx.deps.state
    state.facts = [f for f in state.facts if f.key != key]
    state.facts.append(Fact(key=key, label=label, value=value))
    state.card = None
    return _snapshot(state)


def new_deps() -> InterviewDeps:
    """Fresh per-request dependencies (the adapter mutates the state).

    Returns:
        Empty shared state wrapped in ``StateDeps``.
    """
    return StateDeps(InterviewState())
