"""The interview agent: talks to the host, saves what it hears, shows cards.

It runs on Qwen (``tuttitrip:agent``: the GB10, then OpenRouter) in this
backend because the host waits for it (decision D2). It only drafts: every
value goes through the ``trips`` and ``profiles`` services with the membership
from ``deps``; which question comes next is decided by
``logic.next_question``, the model only words it.
"""

import hashlib
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
from tuttitrip.interview.services import question_service, session_service
from tuttitrip.interview.services.card_tools import card_toolset
from tuttitrip.interview.services.interview_deps import InterviewDeps
from tuttitrip.interview.services.member_tools import member_toolset
from tuttitrip.interview.services.plan_tools import plan_toolset
from tuttitrip.interview.services.preference_tools import preference_toolset
from tuttitrip.interview.services.tool_support import Ctx, for_host, for_member
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
- If a tool says NOT SAVED, the organizer set that value themselves: ask them \
in your reply and stop. Only after they agree in a later message call the tool \
again with overwrite_host_values=true; never set it in the same turn.
- When the organizer asks to see the plan at any point ("Zbuduj plan", "pokaż \
plan"), call build_plan_now. It needs only the city; it lists the assumptions \
it made (say them in one sentence) and the interview goes on. If it says NOT \
BUILT, ask for the city.
- When there is no next question, say what you know and offer: "Zbuduj plan".
"""


MEMBER_INSTRUCTIONS = """\
You are the TuttiTrip assistant. You talk with a member of a group trip, not \
with the organizer, about THEIR OWN interests, so the trip fits them better. \
Answer in the language of the member (Polish by default), briefly and warmly.

Rules:
- Everything the member says about their own interests, diet, access limits or \
what matters to them must be saved with a tool in the same turn. Never write \
that you saved something unless the tool returned a result.
- Call tools one at a time. The tools write only to the member's own profile; \
none of them takes a person.
- You cannot change the trip's budget, dates, the list of people or anybody \
else's preferences, and you have no tool for it. If the member asks, say that \
the organizer does that, and do not pretend to save it.
- Ask one question at a time with show_card, using the card kind, field and \
options of "Next question" below, and write at most one short sentence besides \
the card. The member's answer to a card arrives as their next message.
- When there is no next question, say what you know about them and thank them.
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


def base_instructions(ctx: RunContext[InterviewDeps]) -> str:
    """The standing rules: the organizer's interview, or a member's own.

    Args:
        ctx: The run context.

    Returns:
        The instructions for the role of the caller.
    """
    return MEMBER_INSTRUCTIONS if ctx.deps.is_member else INSTRUCTIONS


interview_agent: Agent[InterviewDeps, str] = Agent(
    model_id(ModelKey.AGENT),
    deps_type=InterviewDeps,
    instructions=base_instructions,
    toolsets=[
        trip_toolset.filtered(for_host),
        preference_toolset.filtered(for_host),
        plan_toolset.filtered(for_host),
        member_toolset.filtered(for_member),
        card_toolset,
    ],
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
    asked = asked_questions(ctx.messages)
    async with ctx.deps.sessions() as session:
        known = await session_service.get_knowledge(session, ctx.deps.membership)
        # The model asks for its instructions on every request of a turn; the
        # solver measures once per state of the panel.
        key = hashlib.sha256(
            f"{known.model_dump_json()}{sorted(map(str, asked))}".encode()
        ).hexdigest()
        if key not in ctx.deps.chosen:
            ctx.deps.chosen[key] = (
                next_question.member_question(known, asked)
                if ctx.deps.is_member
                else await question_service.choose(
                    session, ctx.deps.membership, known, asked
                )
            )
        question = ctx.deps.chosen[key]
    ctx.deps.state.knowledge = known
    facts = (
        constants.MEMBER_TRIP_FACTS
        if ctx.deps.is_member
        else constants.TRIP_FACT_FIELDS
    )
    trip = known.trip.model_dump(mode="json", include=set(facts))
    people = [
        {"person_id": str(p.id), "name": p.display_name, "age": p.age}
        for p in known.people
    ]
    missing = [m.field.value for m in known.missing]
    nothing = (
        "none: thank them" if ctx.deps.is_member else "none: offer to build the plan"
    )
    next_text = question.model_dump_json() if question else nothing
    calendar_text = "; ".join(calendar.upcoming_days(date.today()))  # ruff: ignore[call-date-today] the server's own calendar day
    currency = known.trip.currency or constants.DEFAULT_CURRENCY
    return (
        f"Calendar: {calendar_text}.\n"
        f"Currency: {currency} (amounts the host gives are in it unless they "
        "name another currency; do not ask).\n"
        f"Trip: {trip}\nPeople: {people}\nStill missing: {missing}\n"
        f"Next question: {next_text}"
    )
