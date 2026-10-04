"""The interview agent: talks to the host, saves what it hears, shows cards.

It runs on Qwen (``tuttitrip:agent``: the GB10, then OpenRouter) in this
backend because the host waits for it (decision D2). It only drafts: every
value goes through the ``trips`` and ``profiles`` services with the membership
from ``deps``; which question comes next is decided by
``logic.next_question``, the model only words it.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai_harness import InputGuardrail, SpendLimits
from pydantic_ai_harness.guardrails.detectors import redact_secrets
from pydantic_ai_harness.repair_tool_arguments import RepairToolArguments
from pydantic_ai_harness.spend import Budget

from tuttitrip.interview import constants
from tuttitrip.interview.logic import calendar, next_question
from tuttitrip.interview.schemas import (
    QuestionField,
    QuestionKey,
)
from tuttitrip.interview.services import session_service
from tuttitrip.interview.services.card_tools import card_toolset
from tuttitrip.interview.services.interview_deps import InterviewDeps
from tuttitrip.interview.services.preference_tools import preference_toolset
from tuttitrip.interview.services.tool_support import Ctx
from tuttitrip.interview.services.trip_tools import trip_toolset
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.llm.services.model_catalog import ModelKey, catalog, model_id

QWEN_NAME_MARK = "qwen"

INSTRUCTIONS = """\
You are the TuttiTrip assistant. You interview the organizer of a group trip \
and fill in the plan's inputs. Answer in the language of the organizer (Polish \
by default), briefly and warmly.

Rules:
- Everything the organizer says about the trip, the people, the budget, \
limits, diet or interests must be saved with a tool in the same turn. Never \
write that you saved something unless the tool returned a result.
- Call tools one at a time. Use person_id values from the tools or from the \
state below, never names. The organizer is already in the group.
- Dates: use the calendar below to turn "od soboty" into a date; \
set_trip_basics takes a start date and a number of days.
- Ask one question at a time with show_card, using the card kind, field and \
options of "Next question" below, and write at most one short sentence besides \
the card. The organizer's answer to a card arrives as their next message.
- If a tool says NOT SAVED, ask the organizer and call it again only as it \
says.
- When there is no next question, say what you know and offer: "Zbuduj plan".
"""


def _price(response: ModelResponse) -> Decimal | None:
    """Estimated cost of a Qwen answer; other models are priced by genai-prices.

    Args:
        response: A model response of the run.

    Returns:
        The cost in USD, or None to let the registry price it.
    """
    if QWEN_NAME_MARK not in (response.model_name or "").lower():
        return None
    interview = get_settings().interview
    return (
        interview.qwen_usd_per_million_input_tokens * response.usage.input_tokens
        + interview.qwen_usd_per_million_output_tokens * response.usage.output_tokens
    ) / constants.TOKENS_PER_PRICE_UNIT


def _trip_scope(ctx: RunContext[InterviewDeps]) -> str:
    return str(ctx.deps.membership.trip_id)


interview_agent: Agent[InterviewDeps, str] = Agent(
    model_id(ModelKey.AGENT),
    deps_type=InterviewDeps,
    instructions=INSTRUCTIONS,
    toolsets=[trip_toolset, preference_toolset, card_toolset],
    model_settings={"parallel_tool_calls": False},
    capabilities=[
        catalog.capability(),
        SpendLimits(
            budgets=[
                Budget(
                    usd=get_settings().interview.trip_budget_usd,
                    window="total",
                    scope=_trip_scope,
                    name="interview-trip",
                )
            ],
            price=_price,
        ),
        InputGuardrail(guard=[redact_secrets]),
        RepairToolArguments(),
    ],
    defer_model_check=True,
)


def asked_questions(messages: Sequence[ModelMessage]) -> set[QuestionKey]:
    """Questions already put on screen, read back from the stored history.

    Args:
        messages: The history including this run's messages.

    Returns:
        One key per ``show_card`` call that named a field.
    """
    keys: set[QuestionKey] = set()
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if (
                isinstance(part, ToolCallPart)
                and part.tool_name == constants.SHOW_CARD_TOOL
            ):
                args = part.args_as_dict()
                try:
                    keys.add(
                        QuestionKey(
                            field=QuestionField(args["field"]),
                            person_id=UUID(args["person_id"])
                            if args.get("person_id")
                            else None,
                        )
                    )
                except KeyError, ValueError:
                    continue
    return keys


@interview_agent.instructions
async def current_context(ctx: Ctx) -> str:
    """Tell the model today's calendar, what is known and what to ask next.

    Args:
        ctx: The run context.

    Returns:
        Text appended to the instructions on every request.
    """
    async with ctx.deps.sessions() as session:
        known = await session_service.get_knowledge(session, ctx.deps.membership)
    ctx.deps.state.knowledge = known
    question = next_question.next_question(known, asked_questions(ctx.messages))
    trip = known.trip.model_dump(mode="json", include=set(constants.TRIP_FACT_FIELDS))
    people = [
        {"person_id": str(p.id), "name": p.display_name, "age": p.age}
        for p in known.people
    ]
    missing = [m.field.value for m in known.missing]
    next_text = (
        question.model_dump_json() if question else "none: offer to build the plan"
    )
    calendar_text = "; ".join(calendar.upcoming_days(date.today()))  # ruff: ignore[call-date-today] the server's own calendar day
    return (
        f"Calendar: {calendar_text}.\n"
        f"Trip: {trip}\nPeople: {people}\nStill missing: {missing}\n"
        f"Next question: {next_text}"
    )
