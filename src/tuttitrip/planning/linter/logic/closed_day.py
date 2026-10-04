"""Rule ``closed_day``: no visit on a day the place is closed (E0)."""

from zoneinfo import ZoneInfo

from tuttitrip.planning.linter.logic.rule import Rule, stops_by_day
from tuttitrip.planning.linter.schemas import Finding, LintContext, LintPlan, Severity
from tuttitrip.planning.logic.schedule import is_open_on

CODE = "closed_day"


def check(plan: LintPlan, context: LintContext) -> list[Finding]:
    """Flag visits on a closed weekday or a closed date (verified hours only).

    Args:
        plan: The plan.
        context: Places and the city's zone.

    Returns:
        One violation per stop on a closed day.
    """
    zone = ZoneInfo(context.timezone)
    return [
        stop.finding(CODE, Severity.VIOLATION, f"{stop.item.name} is closed on {day}")
        for day, stops in stops_by_day(plan, context)
        for stop in stops
        if stop.place is not None
        and stop.place.hours.opening_hours is not None
        and stop.place.hours.verified
        and not is_open_on(stop.place, day, zone)
    ]


RULE = Rule(CODE, 5, check)
