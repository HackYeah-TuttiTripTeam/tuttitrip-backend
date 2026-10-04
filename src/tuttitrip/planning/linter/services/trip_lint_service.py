"""Lint a trip's own plan or a plan given as names and times.

The context (catalog, people, zone, budget) comes from the same input the
planner uses, so the numbers equal those of ``POST /planning/linter/check`` for
the same plan. Findings about one person (their walking, stairs or nap limits
reveal health data) are generalised for callers below the co-host role.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.linter.logic import trip_lint
from tuttitrip.planning.linter.schemas import (
    Finding,
    LintDay,
    LintItem,
    LintPlan,
    LintReport,
    LintRequest,
    NamedPlan,
    RuleResult,
)
from tuttitrip.planning.linter.services import linter_service
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.trips.schemas import TripMembership, TripRole

HIDDEN_PERSON = "A person of the group"
HIDDEN_MESSAGE = "This stop does not suit one person of the group"


def _hide(finding: Finding) -> Finding:
    if finding.person_id is None:
        return finding
    return finding.model_copy(
        update={
            "person_id": None,
            "person_name": None,
            "message": f"{finding.place_name or HIDDEN_PERSON}: {HIDDEN_MESSAGE}",
        }
    )


def _limited(report: LintReport) -> LintReport:
    results = [
        RuleResult(
            rule=r.rule,
            weight=r.weight,
            count=r.count,
            violations=[_hide(f) for f in r.violations],
            warnings=[_hide(f) for f in r.warnings],
        )
        for r in report.results
    ]
    return report.model_copy(update={"results": results})


def _for(membership: TripMembership, report: LintReport) -> LintReport:
    return report if membership.role.satisfies(TripRole.CO_HOST) else _limited(report)


async def lint_latest_plan(
    session: AsyncSession, membership: TripMembership
) -> LintReport:
    """Lint the newest stored plan of the trip.

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.

    Returns:
        The report; person-specific findings are generalised below co-host.

    Raises:
        PlanNotFoundError: When the trip has no plan yet.
        PlanInputError: When the trip lacks dates, a city or people.
    """
    plan = await plan_service.latest_plan(session, membership)
    planning, names, _ = await plan_service.gather_input(session, membership)
    request = LintRequest(
        plan=trip_lint.plan_of(plan, planning.trip.days, len(planning.people)),
        context=trip_lint.context_of(planning, names),
    )
    return _for(membership, linter_service.check_plan(request))


async def lint_named_plan(
    session: AsyncSession, membership: TripMembership, named: NamedPlan
) -> LintReport:
    """Lint a plan given as days of named stops, matched to the city's catalog.

    A name that is not clearly one catalog place stays unrecognised and counts
    as an ``unknown_place`` violation.

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.
        named: The days, stops and times.

    Returns:
        The report; person-specific findings are generalised below co-host.

    Raises:
        PlanInputError: When the trip lacks dates, a city or people.
    """
    planning, names, _ = await plan_service.gather_input(session, membership)
    context = trip_lint.context_of(planning, names)
    places = list(planning.places)
    days = []
    for day in named.days:
        items = []
        for stop in day.items:
            place = trip_lint.match_place(stop.name, places)
            items.append(
                LintItem(
                    name=stop.name,
                    place_id=place.id if place else None,
                    start=stop.start,
                    end=stop.end,
                )
            )
        days.append(LintDay(day=day.day, items=items))
    report = linter_service.check_plan(
        LintRequest(plan=LintPlan(days=days), context=context)
    )
    return _for(membership, report)
