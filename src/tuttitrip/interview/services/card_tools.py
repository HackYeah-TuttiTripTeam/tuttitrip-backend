"""The card tool: puts one question on the organizer's screen."""

from uuid import UUID

from pydantic_ai import ModelRetry, RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from tuttitrip.interview.schemas import CardKind, QuestionField, ShownCard
from tuttitrip.interview.services.interview_deps import InterviewDeps
from tuttitrip.interview.services.tool_support import snapshot, tool_session

card_toolset = FunctionToolset[InterviewDeps]()


@card_toolset.tool
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
