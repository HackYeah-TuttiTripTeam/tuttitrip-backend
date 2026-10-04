"""Rule ``unknown_place``: every stop should be a place from the catalog."""

from tuttitrip.planning.linter.logic.rule import Rule, stops_by_day
from tuttitrip.planning.linter.schemas import Finding, LintContext, LintPlan, Severity

CODE = "unknown_place"


def check(plan: LintPlan, context: LintContext) -> list[Finding]:
    """Flag stops that were not recognised (no ``place_id`` or not in the catalog).

    Args:
        plan: The plan.
        context: Catalog places.

    Returns:
        One violation per unrecognised stop.
    """
    return [
        stop.finding(CODE, Severity.VIOLATION, f"{stop.item.name} is not a known place")
        for _, stops in stops_by_day(plan, context)
        for stop in stops
        if stop.place is None
    ]


RULE = Rule(CODE, 1, check)
