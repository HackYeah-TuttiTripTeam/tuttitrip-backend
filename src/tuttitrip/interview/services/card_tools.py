"""The card tool: puts one question on the organizer's screen."""

from uuid import UUID

from pydantic_ai import ModelRetry, RunContext, ToolReturn
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

from tuttitrip.interview.schemas import CardKind, QuestionField, ShownCard
from tuttitrip.interview.services.interview_deps import InterviewDeps
from tuttitrip.interview.services.tool_support import snapshot, tool_session

card_toolset = FunctionToolset[InterviewDeps]()


async def hide_in_voice(  # ruff: ignore[unused-async] a tool prepare hook is async
    ctx: RunContext[InterviewDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    """Offer ``show_card`` only where there is a screen to show it on.

    Args:
        ctx: The run context.
        tool_def: The tool as defined.

    Returns:
        The tool, or None on a voice call.
    """
    return None if ctx.deps.voice else tool_def


@card_toolset.tool(prepare=hide_in_voice)
async def show_card(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] the tool schema
    ctx: RunContext[InterviewDeps],
    kind: CardKind,
    question: str,
    options: list[str] | None = None,
    field: QuestionField | None = None,
    person_id: UUID | None = None,
) -> ToolReturn:
    """Put a question card on the organizer's screen.

    Use the kind, field, person_id and options of "Next question". The
    organizer's answer comes as their next message.

    Args:
        ctx: The run context.
        kind: The card to render.
        question: The question in the organizer's language.
        options: Choices on the card; copy them from "Next question".
        field: What the question is about.
        person_id: The person the question is about, if any.

    Returns:
        A snapshot whose ``card`` the client renders.
    """
    if not question.strip():
        msg = "The question must not be empty"
        raise ModelRetry(msg)
    ctx.deps.state.card = ShownCard(
        kind=kind,
        question=question,
        field=field,
        person_id=person_id,
        options=options or [],
    )
    async with tool_session(ctx) as session:
        return await snapshot(ctx, session, "Card shown; wait for the answer.")
