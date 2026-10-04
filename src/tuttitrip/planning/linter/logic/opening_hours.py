"""Rule ``opening_hours``: the visit must lie inside an opening interval (E0)."""

from zoneinfo import ZoneInfo

from tuttitrip.planning.linter.logic.rule import Rule, stops_by_day
from tuttitrip.planning.linter.schemas import Finding, LintContext, LintPlan, Severity
from tuttitrip.planning.logic.schedule import fits_opening_hours, is_open_on

CODE = "opening_hours"


def check(plan: LintPlan, context: LintContext) -> list[Finding]:
    """Flag visits outside the opening hours; warn about unverified hours.

    A closed day belongs to ``closed_day`` and is skipped here. Hours that
    are missing or not verified give a warning, never a violation.

    Args:
        plan: The plan.
        context: Places and the city's zone.

    Returns:
        Findings in plan order.
    """
    zone = ZoneInfo(context.timezone)
    findings: list[Finding] = []
    for day, stops in stops_by_day(plan, context):
        for stop in stops:
            place = stop.place
            if place is None:
                continue
            if place.hours.opening_hours is None or not place.hours.verified:
                findings.append(
                    stop.finding(
                        CODE,
                        Severity.WARNING,
                        f"Opening hours of {stop.item.name} are unverified",
                    )
                )
                continue
            minutes = stop.minutes
            if minutes is None or not is_open_on(place, day, zone):
                continue
            if not fits_opening_hours(place, day, stop.item.start, minutes, zone):
                findings.append(
                    stop.finding(
                        CODE,
                        Severity.VIOLATION,
                        f"{stop.item.name} on {day}: the {minutes} min visit from "
                        f"{stop.item.start:%H:%M} is outside the opening hours",
                    )
                )
    return findings


RULE = Rule(CODE, 4, check)
