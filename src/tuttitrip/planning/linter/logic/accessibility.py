"""Rule ``accessibility``: stairs and wheelchair access of a stop per person."""

from tuttitrip.planning.linter.logic.rule import Rule, stops_by_day
from tuttitrip.planning.linter.schemas import Finding, LintContext, LintPlan, Severity
from tuttitrip.planning.logic.params import DEFAULT_PARAMS

CODE = "accessibility"
STAIRS_LIMIT = DEFAULT_PARAMS.stairs_limit
"""E0: a place is out when ``stairs_p * sensitivity_i`` reaches this."""


def check(plan: LintPlan, context: LintContext) -> list[Finding]:
    """Flag stops a person cannot reach.

    Violations: ``stairs_p * sensitivity_i >= 0.9`` (E0), and for a wheelchair
    user ``wheelchair = no`` or stairs without a confirmed ``wheelchair = yes``
    (no lift data exists, so yes stands for step-free access). An unknown
    ``wheelchair`` with no stairs is a warning, not a violation.

    Args:
        plan: The plan.
        context: Places (``stairs``, ``wheelchair``) and people.

    Returns:
        One finding per person and stop.
    """
    findings: list[Finding] = []
    for _, stops in stops_by_day(plan, context):
        for stop in stops:
            place = stop.place
            if place is None:
                continue
            for person in context.people:
                reason = None
                severity = Severity.VIOLATION
                if place.stairs * person.stairs_sensitivity >= STAIRS_LIMIT:
                    reason = "has stairs"
                elif person.wheelchair and place.wheelchair is False:
                    reason = "is not wheelchair accessible"
                elif person.wheelchair and place.wheelchair is None:
                    if place.stairs > 0:
                        reason = "has stairs and no confirmed step-free access"
                    else:
                        reason = "has unknown wheelchair access"
                        severity = Severity.WARNING
                if reason is None:
                    continue
                findings.append(
                    stop.finding(
                        CODE,
                        severity,
                        f"{person.name}: {stop.item.name} {reason}",
                        person,
                    )
                )
    return findings


RULE = Rule(CODE, 4, check)
