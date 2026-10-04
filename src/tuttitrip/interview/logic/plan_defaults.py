"""Fill the gaps of a preliminary plan with explicit assumptions.

Pure and deterministic: the same knowledge and the same day give the same
assumptions (so the same plan hash). The one thing a plan cannot do without is
the city; everything else is assumed and listed for the host, who corrects it
by answering the interview. Assumptions are never stored on the trip.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from tuttitrip.interview import constants
from tuttitrip.interview.logic import knowledge as knowledge_logic
from tuttitrip.interview.schemas import Assumption, AssumptionCode, KnowledgeRead
from tuttitrip.planning.plans.schemas import PlanAssumptions


class MissingCityError(Exception):
    """The trip has no city yet, so there is nothing to plan."""


@dataclass(frozen=True)
class DraftSpec:
    """What to fill in and what to tell the host."""

    assumptions: PlanAssumptions
    notes: list[Assumption]


def _next_weekday(today: date, weekday: int) -> date:
    """The first ``weekday`` strictly after ``today``.

    Args:
        today: The day to count from.
        weekday: Weekday number, Monday is 0.

    Returns:
        The date.
    """
    ahead = (weekday - today.weekday()) % constants.DAYS_IN_WEEK
    return today + timedelta(days=ahead or constants.DAYS_IN_WEEK)


def plan_defaults(knowledge: KnowledgeRead, today: date) -> DraftSpec:
    """Decide what a preliminary plan assumes for the data the trip lacks.

    Args:
        knowledge: The "What we already know" view.
        today: The server's calendar day (the assumed day is counted from it).

    Returns:
        The gaps to fill in memory and the assumptions in Polish, in a fixed order.

    Raises:
        MissingCityError: The trip has no city.
    """
    trip = knowledge.trip
    if trip.city_slug is None:
        raise MissingCityError
    notes: list[Assumption] = []
    start: date | None = None
    end: date | None = None
    if trip.start_date is None and trip.end_date is None:
        start = _next_weekday(today, constants.DRAFT_WEEKDAY)
        end = start + timedelta(days=constants.DRAFT_DEFAULT_DAYS - 1)
        notes.append(
            Assumption(
                code=AssumptionCode.DATES,
                params={
                    "start_date": start.isoformat(),
                    "days": constants.DRAFT_DEFAULT_DAYS,
                },
                text=constants.ASSUMPTION_DATES_PL.format(
                    date=start.strftime(constants.ASSUMED_DATE_FORMAT)
                ),
            )
        )
    elif trip.start_date is None:
        start = trip.end_date  # a lone end date: one day, that day
    elif trip.end_date is None:
        end = trip.start_date
    if len(knowledge.people) < knowledge_logic.MIN_PEOPLE:
        notes.append(
            Assumption(
                code=AssumptionCode.PEOPLE,
                params={"adults": knowledge_logic.MIN_PEOPLE},
                text=constants.ASSUMPTION_PEOPLE_PL,
            )
        )
    if trip.budget_total_max is None:
        notes.append(
            Assumption(code=AssumptionCode.BUDGET, text=constants.ASSUMPTION_BUDGET_PL)
        )
    if not all(p.filled for p in knowledge.preferences):
        notes.append(
            Assumption(
                code=AssumptionCode.PREFERENCES,
                text=constants.ASSUMPTION_PREFERENCES_PL,
            )
        )
    return DraftSpec(
        assumptions=PlanAssumptions(
            start_date=start, end_date=end, min_people=knowledge_logic.MIN_PEOPLE
        ),
        notes=notes,
    )
