"""Rule ``transfer``: enough gap for the catalog transfer ``transfer_p``."""

from itertools import pairwise

from tuttitrip.planning.linter.logic.rule import Rule, stops_by_day
from tuttitrip.planning.linter.schemas import Finding, LintContext, LintPlan, Severity

CODE = "transfer"
SECONDS_PER_MINUTE = 60


def check(plan: LintPlan, context: LintContext) -> list[Finding]:
    """Flag a stop that starts sooner than the previous one ends plus transfer.

    The schedule puts ``transfer_min`` of the place before it (not before the
    first stop of a day); the linter requires the same gap.

    Args:
        plan: The plan.
        context: Places with their ``transfer_min``.

    Returns:
        One violation per too-tight stop.
    """
    findings: list[Finding] = []
    for _, stops in stops_by_day(plan, context):
        for previous, stop in pairwise(stops):
            previous_end = previous.end
            if stop.place is None or previous_end is None:
                continue
            gap = int((stop.begin - previous_end).total_seconds() // SECONDS_PER_MINUTE)
            if gap < stop.place.transfer_min:
                findings.append(
                    stop.finding(
                        CODE,
                        Severity.VIOLATION,
                        f"{stop.item.name}: {gap} min after {previous.item.name}, "
                        f"needs {stop.place.transfer_min} min",
                    )
                )
    return findings


RULE = Rule(CODE, 2, check)
