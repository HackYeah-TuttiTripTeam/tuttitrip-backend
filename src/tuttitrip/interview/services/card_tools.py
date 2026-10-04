"""The card tool: puts one question on the organizer's screen."""

from uuid import UUID

from pydantic_ai import ModelRetry, RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from tuttitrip.interview import constants
from tuttitrip.interview.logic import next_question
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

    Use the field and person_id of "Next question" and word the question. The
    server fixes the card for a field and its options; yours are used only for
    a card without a field. The answer comes as the organizer's next message.

    Args:
        ctx: The run context.
        kind: The card to render (replaced by the card of the field, if any).
        question: The question in the organizer's language.
        options: Choices of a card without a field, e.g. a confirmation.
        field: What the question is about.
        person_id: The person the question is about, if any.

    Returns:
        A snapshot whose ``card`` the client renders.
    """
    if not question.strip():
        msg = "The question must not be empty"
        raise ModelRetry(msg)
    if field is not None:
        kind = constants.CARD_OF_FIELD[field]
        options = next_question.options_for(field) or options
    ctx.deps.state.card = ShownCard(
        kind=kind,
        question=question,
        field=field,
        person_id=person_id,
        options=[] if kind in constants.NO_OPTION_CARDS else options or [],
    )
    async with tool_session(ctx) as session:
        return await snapshot(ctx, session, "Card shown; wait for the answer.")
