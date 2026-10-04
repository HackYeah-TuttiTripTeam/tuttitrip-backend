"""What every rule shares: the rule type and the plan flattened into stops."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.linter.schemas import (
    Finding,
    LintContext,
    LintItem,
    LintPlan,
    Severity,
)


@dataclass(frozen=True, slots=True)
class Stop:
    """A stop of the plan with its catalog place (None when not recognised)."""

    day: date
    position: int
    item: LintItem
    place: PlaceRead | None

    @property
    def minutes(self) -> int | None:
        """Visit length: the stated end, else the typical visit; None if neither."""
        if self.item.end is not None:
            return _minutes_between(self.day, self.item.start, self.item.end)
        return None if self.place is None else self.place.typical_visit_min

    @property
    def end(self) -> datetime | None:
        """Naive local end of the visit, None when its length is unknown."""
        minutes = self.minutes
        if minutes is None:
            return None
        return self.begin + timedelta(minutes=minutes)

    @property
    def begin(self) -> datetime:
        """Naive local arrival."""
        return datetime.combine(self.day, self.item.start)

    def finding(self, rule: str, severity: Severity, message: str) -> Finding:
        """Build a finding pointing at this stop.

        Args:
            rule: Code of the rule.
            severity: Violation or warning.
            message: Human-readable explanation.

        Returns:
            The finding with day, position and place name.
        """
        return Finding(
            rule=rule,
            severity=severity,
            message=message,
            day=self.day,
            position=self.position,
            place_name=self.item.name,
        )


def _minutes_between(day: date, start: time, end: time) -> int:
    delta = datetime.combine(day, end) - datetime.combine(day, start)
    return int(delta.total_seconds() // 60)


@dataclass(frozen=True, slots=True)
class Rule:
    """A lint rule: a code, an explicit weight and a pure check."""

    code: str
    weight: int
    check: Callable[[LintPlan, LintContext], list[Finding]]


def stops_by_day(
    plan: LintPlan, context: LintContext
) -> Iterator[tuple[date, list[Stop]]]:
    """Walk the plan day by day, stops in chronological order.

    Args:
        plan: The plan.
        context: Catalog places to match ``place_id`` against.

    Yields:
        Each date (ascending) with its stops sorted by arrival, then by the
        order sent; ``position`` stays the index as sent.
    """
    places = {p.id: p for p in context.places}
    for day in sorted(plan.days, key=lambda d: d.day):
        stops = [
            Stop(
                day.day,
                position,
                item,
                None if item.place_id is None else places.get(item.place_id),
            )
            for position, item in enumerate(day.items)
        ]
        yield day.day, sorted(stops, key=lambda s: (s.item.start, s.position))
