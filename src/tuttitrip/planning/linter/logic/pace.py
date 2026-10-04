"""Rule ``pace``: the distance of a day against ``D_i`` of each person."""

from tuttitrip.planning.linter.logic.rule import Rule, stops_by_day
from tuttitrip.planning.linter.schemas import Finding, LintContext, LintPlan, Severity
from tuttitrip.planning.logic.schedule import DAILY_KM_FACTOR, daily_distance_km

CODE = "pace"

_EPS = 1e-9


def check(plan: LintPlan, context: LintContext) -> list[Finding]:
    """Flag days longer than a person walks.

    ``L_d`` is the sum of ``segment_km`` of the day, the same sum the schedule
    uses (``daily_distance_km``). ``L_d > 1.5 * D_i`` is a violation (E0);
    between ``D_i`` and that it is a warning (E2 scores it down). The finding
    points at the stop where the running total first crossed the limit.

    Args:
        plan: The plan.
        context: Places (``segment_km``) and people (``daily_km`` as ``D_i``).

    Returns:
        At most one finding per person and day.
    """
    findings: list[Finding] = []
    for day, stops in stops_by_day(plan, context):
        walked = [(s, s.place.segment_km) for s in stops if s.place is not None]
        total = daily_distance_km(km for _, km in walked)
        for person in context.people:
            limit = DAILY_KM_FACTOR * person.daily_km
            if total > limit + _EPS:
                severity, bound = Severity.VIOLATION, limit
            elif total > person.daily_km + _EPS:
                severity, bound = Severity.WARNING, person.daily_km
            else:
                continue
            tipping = next(
                stop
                for i, (stop, _) in enumerate(walked)
                if daily_distance_km(km for _, km in walked[: i + 1]) > bound + _EPS
            )
            findings.append(
                tipping.finding(
                    CODE,
                    severity,
                    f"{person.name}: {total:g} km on {day}, walks "
                    f"{person.daily_km:g} km a day (limit {limit:g} km)",
                    person,
                )
            )
    return findings


RULE = Rule(CODE, 3, check)
