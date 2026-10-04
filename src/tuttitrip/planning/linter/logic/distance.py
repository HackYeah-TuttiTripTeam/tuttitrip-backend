"""Rule ``distance``: the walking segment of a stop against ``s_i`` of each person."""

from tuttitrip.planning.linter.logic.rule import Rule, stops_by_day
from tuttitrip.planning.linter.schemas import Finding, LintContext, LintPlan, Severity
from tuttitrip.planning.logic.params import DEFAULT_PARAMS

CODE = "distance"
SEGMENT_KM_FACTOR = DEFAULT_PARAMS.segment_factor
"""E0: a segment over this many times ``s_i`` is out; up to ``s_i`` is free."""

_EPS = 1e-9


def check(plan: LintPlan, context: LintContext) -> list[Finding]:
    """Flag segments the person cannot walk in one go.

    ``d_p > 1.5 * s_i`` is a violation (E0). Between ``s_i`` and ``1.5 * s_i``
    the plan may still be built, E2 only scores it down, so it is a warning.

    Args:
        plan: The plan.
        context: Places (``segment_km``) and people (``segment_km`` as ``s_i``).

    Returns:
        One finding per person and stop.
    """
    findings: list[Finding] = []
    for _, stops in stops_by_day(plan, context):
        for stop in stops:
            if stop.place is None:
                continue
            km = stop.place.segment_km
            for person in context.people:
                if km > SEGMENT_KM_FACTOR * person.segment_km + _EPS:
                    severity = Severity.VIOLATION
                elif km > person.segment_km + _EPS:
                    severity = Severity.WARNING
                else:
                    continue
                findings.append(
                    stop.finding(
                        CODE,
                        severity,
                        f"{person.name}: {km:g} km on foot at {stop.item.name}, "
                        f"walks {person.segment_km:g} km in one go "
                        f"(limit {SEGMENT_KM_FACTOR * person.segment_km:g} km)",
                        person,
                    )
                )
    return findings


RULE = Rule(CODE, 3, check)
