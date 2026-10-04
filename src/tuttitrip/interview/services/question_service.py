"""Pick the next question: the one whose answer changes the plan most (issue #62).

The solver measures every candidate question (planning's ``what_if``, outside
the event loop, once per change of what is known); the language model only
words the question. When the measurement is not possible (no city yet, trip
data that cannot be planned) or takes longer than
``interview.impact_budget_seconds``, the fixed table of ``next_question`` decides.
"""

from collections.abc import Collection
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.interview.logic import informativeness, next_question, plan_defaults
from tuttitrip.interview.logic.plan_defaults import MissingCityError
from tuttitrip.interview.schemas import KnowledgeRead, NextQuestion, QuestionKey
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.plans.services.plan_service import PlanInputError
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.trips.schemas import TripMembership


async def choose(
    session: AsyncSession,
    membership: TripMembership,
    known: KnowledgeRead,
    asked: Collection[QuestionKey],
    today: date | None = None,
) -> NextQuestion | None:
    """Choose the next question by its measured impact on the plan.

    Args:
        session: Open session.
        membership: The caller's membership.
        known: The "What we already know" view.
        asked: Questions the assistant already put on screen.
        today: The calendar day the assumed date is counted from (tests); the
            server's day when omitted.

    Returns:
        The question, or None when there is nothing left to ask. Its ``impact``
        is set when the plan was measured, otherwise the fixed order decided.
    """
    candidates = next_question.open_questions(known, asked)
    fixed = next(iter(candidates), None)
    targets = tuple(
        t for q in candidates if (t := informativeness.target_of(q)) is not None
    )
    if fixed is None or known.trip.city_slug is None or not targets:
        return fixed
    try:
        spec = plan_defaults.plan_defaults(
            known,
            today or date.today(),  # ruff: ignore[call-date-today] the server's own calendar day
        )
        scores = await plan_service.measure_impacts(
            session,
            membership,
            spec.assumptions,
            targets,
            get_settings().interview.impact_budget_seconds,
        )
    except MissingCityError, PlanInputError:
        return fixed
    if scores is None:
        return fixed
    return informativeness.pick(candidates, scores)
