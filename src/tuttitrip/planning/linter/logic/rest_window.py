"""Rule ``rest_window``: a break in the nap window and in the lunch window."""

from datetime import date, datetime, timedelta

from tuttitrip.planning.linter.logic.rule import Rule, Stop, stops_by_day
from tuttitrip.planning.linter.schemas import (
    Finding,
    LintContext,
    LintLunch,
    LintPlan,
    Severity,
)

CODE = "rest_window"


def _busy(stops: list[Stop]) -> list[tuple[datetime, datetime]]:
    # Visits whose end is known (stated, or the typical visit of the place).
    return [(s.begin, s.end) for s in stops if s.end is not None]


def _lunch_missing(day: date, stops: list[Stop], lunch: LintLunch) -> bool:
    # Some free gap of ``minutes`` must start in the window: at its start or
    # right after a visit that ends in it.
    length = timedelta(minutes=lunch.minutes)
    earliest = datetime.combine(day, lunch.earliest)
    latest = datetime.combine(day, lunch.latest)
    busy = _busy(stops)
    starts = [earliest, *(end for _, end in busy if earliest < end <= latest)]
    return not any(
        all(start + length <= b or end <= start for b, end in busy) for start in starts
    )


def check(plan: LintPlan, context: LintContext) -> list[Finding]:
    """Flag visits that take the nap of a person and days with no lunch gap.

    A visit that overlaps the nap (start to start + minutes) is a violation for
    that person. When the context has a lunch window, a day with a stop and
    without a free gap of the lunch length starting in it is a violation
    pointing at the day (the last stop), since lunch is shared by the group.
    Stops of unknown length are skipped. Times are local to the city.

    Args:
        plan: The plan.
        context: People (nap) and the optional lunch window.

    Returns:
        The findings in day and stop order.
    """
    findings: list[Finding] = []
    naps = [p for p in context.people if p.nap_start is not None and p.nap_minutes > 0]
    for day, stops in stops_by_day(plan, context):
        for stop in stops:
            if stop.end is None:
                continue
            for person in naps:
                if person.nap_start is None:
                    continue
                nap_start = datetime.combine(day, person.nap_start)
                nap_end = nap_start + timedelta(minutes=person.nap_minutes)
                if stop.begin < nap_end and nap_start < stop.end:
                    findings.append(
                        stop.finding(
                            CODE,
                            Severity.VIOLATION,
                            f"{person.name}: {stop.item.name} "
                            f"({stop.begin:%H:%M}-{stop.end:%H:%M}) overlaps the nap "
                            f"({nap_start:%H:%M}-{nap_end:%H:%M})",
                            person,
                        )
                    )
        if (
            stops
            and context.lunch is not None
            and _lunch_missing(day, stops, context.lunch)
        ):
            findings.append(
                stops[-1].finding(
                    CODE,
                    Severity.VIOLATION,
                    f"No {context.lunch.minutes} min free for lunch starting "
                    f"between {context.lunch.earliest:%H:%M} and "
                    f"{context.lunch.latest:%H:%M} on {day}",
                )
            )
    return findings


RULE = Rule(CODE, 2, check)
