"""Rule ``accessibility``: stairs and wheelchair access of a stop per person."""

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.linter.logic.rule import Rule, stops_by_day
from tuttitrip.planning.linter.schemas import (
    Finding,
    LintContext,
    LintPerson,
    LintPlan,
    Severity,
)

CODE = "accessibility"
STAIRS_LIMIT = 0.9
"""E0: a place is out when ``stairs_p * sensitivity_i`` reaches this."""


def _wheelchair_problem(place: PlaceRead) -> tuple[str, Severity] | None:
    # A wheelchair user needs step-free access confirmed (``wheelchair = yes``).
    if place.wheelchair is False:
        return "is not wheelchair accessible", Severity.VIOLATION
    if place.wheelchair is not None:
        return None
    if place.stairs is None:
        return (
            "has unknown stairs and no confirmed step-free access",
            Severity.VIOLATION,
        )
    if place.stairs > 0:
        return "has stairs and no confirmed step-free access", Severity.VIOLATION
    return "has unknown wheelchair access", Severity.WARNING


def _problem(place: PlaceRead, person: LintPerson) -> tuple[str, Severity] | None:
    stairs = place.stairs
    if stairs is not None and stairs * person.stairs_sensitivity >= STAIRS_LIMIT:
        return "has stairs", Severity.VIOLATION
    if person.wheelchair:
        found = _wheelchair_problem(place)
        if found is not None:
            return found
    if (
        stairs is None
        and person.stairs_sensitivity > 0
        and place.wheelchair is not True
    ):
        return "has unknown stairs", Severity.WARNING
    return None


def check(plan: LintPlan, context: LintContext) -> list[Finding]:
    """Flag stops a person cannot reach.

    Violations: ``stairs_p * sensitivity_i >= 0.9`` (E0), and for a wheelchair
    user ``wheelchair = no`` or stairs without a confirmed ``wheelchair = yes``
    (no lift data exists, so yes stands for step-free access). An unknown
    ``wheelchair`` with no stairs is a warning, not a violation. Unknown
    ``stairs`` (null) are never "no stairs": a person with any stairs
    sensitivity gets a warning, a wheelchair user a violation unless the place
    is confirmed step-free.

    Args:
        plan: The plan.
        context: Places (``stairs``, ``wheelchair``) and people.

    Returns:
        One finding per person and stop.
    """
    findings: list[Finding] = []
    for _, stops in stops_by_day(plan, context):
        for stop in stops:
            if stop.place is None:
                continue
            for person in context.people:
                problem = _problem(stop.place, person)
                if problem is None:
                    continue
                reason, severity = problem
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
