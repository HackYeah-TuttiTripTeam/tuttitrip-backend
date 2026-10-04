"""The budget of each day, what was spent and the budget left for the rest (backend#89).

```
day budget   "from" and "to": the trip's own day range when it has one, else the
             trip budget (B_od, B_do) divided by the number of days
margin       B_max = B_do * (1 + flex), the same flex as the trip (E6)
spent        the group's expenses of the day in the categories of the plan
left         B_do of the day - spent (negative: over), and to B_max
overrun      spent > B_do of the day
rest         after the last overrunning day: B_od and B_do of the trip - everything
             spent so far, never below 0; the solver plans the remaining days with it
```

Decision (backend#89, "pitfalls"): expenses outside the plan do not take the budget of
the attractions. Food, transport, activities and expenses without a category count;
shopping (souvenirs), lodging (the nights are the lodging domain) and "other" do not,
and are reported separately as ``outside_plan``. Drafts do not count either: after
backend#192 ``expenses/db.py::select_day_totals`` sums confirmed expenses only.

Pure, standard library plus the schemas of planning and expenses.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from uuid import UUID

from tuttitrip.expenses.schemas import ExpenseCategory, ExpenseDayTotal
from tuttitrip.planning.schemas import PlanningInput, PlanningTrip

COUNTED_CATEGORIES: Final = frozenset(
    {None, ExpenseCategory.FOOD, ExpenseCategory.TRANSPORT, ExpenseCategory.ACTIVITIES}
)
"""Categories that take the budget of the day; the rest is ``outside_plan``."""
_CENT: Final = Decimal("0.01")
_PERCENT: Final = Decimal(100)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(_CENT, ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class DayBudget:
    """One day against its budget."""

    index: int
    """1-based number of the day."""
    day: date
    budget_from: Decimal
    budget_to: Decimal
    budget_max: Decimal
    spent: Decimal
    """Expenses of the day that count against the budget."""
    outside_plan: Decimal
    """Expenses of the day that do not (souvenirs, lodging, other)."""

    @property
    def remaining(self) -> Decimal:
        """``B_do`` of the day minus what was spent; negative when over."""
        return self.budget_to - self.spent

    @property
    def remaining_max(self) -> Decimal:
        """``B_max`` of the day minus what was spent; negative when over."""
        return self.budget_max - self.spent

    @property
    def over_budget(self) -> bool:
        """The day went over ``B_do``."""
        return self.spent > self.budget_to

    @property
    def over_max(self) -> bool:
        """The day went over ``B_max`` (the hard limit of E0)."""
        return self.spent > self.budget_max


def spent_per_day(
    totals: Iterable[ExpenseDayTotal],
) -> tuple[Mapping[date, Decimal], Mapping[date, Decimal]]:
    """Split the expenses of each day into counted and outside the plan.

    Args:
        totals: Sums per day and category.

    Returns:
        ``(counted, outside_plan)`` by day.
    """
    counted: dict[date, Decimal] = {}
    outside: dict[date, Decimal] = {}
    for row in totals:
        target = counted if row.category in COUNTED_CATEGORIES else outside
        target[row.spent_on] = target.get(row.spent_on, Decimal(0)) + row.amount
    return counted, outside


def day_budgets(  # ruff: ignore[too-many-arguments] the whole budget of a trip
    days: Sequence[date],
    *,
    total_from: Decimal,
    total_to: Decimal,
    flex_pct: int,
    day_from: Decimal | None = None,
    day_to: Decimal | None = None,
    totals: Iterable[ExpenseDayTotal] = (),
) -> list[DayBudget]:
    """The budget and spending of every day of the trip.

    Args:
        days: The days of the trip, in date order.
        total_from: ``B_od`` of the trip.
        total_to: ``B_do`` of the trip.
        flex_pct: ``flex`` in percent (E6).
        day_from: The trip's own ``B_od`` of a day, when it set one.
        day_to: The trip's own ``B_do`` of a day, when it set one.
        totals: The expenses summed per day and category.

    Returns:
        One entry per day. Without a day range the trip budget is divided
        equally (to the cent).
    """
    count = len(days)
    share_from = _cents(total_from / count) if day_from is None else day_from
    share_to = _cents(total_to / count) if day_to is None else day_to
    maximum = _cents(share_to * (_PERCENT + flex_pct) / _PERCENT)
    counted, outside = spent_per_day(totals)
    return [
        DayBudget(
            index=index,
            day=day,
            budget_from=share_from,
            budget_to=share_to,
            budget_max=maximum,
            spent=counted.get(day, Decimal(0)),
            outside_plan=outside.get(day, Decimal(0)),
        )
        for index, day in enumerate(days, start=1)
    ]


def last_overrun(budgets: Sequence[DayBudget]) -> DayBudget | None:
    """The latest day that went over its budget.

    Args:
        budgets: The days of the trip.

    Returns:
        The day, or None when no day is over ``B_do``.
    """
    over = [b for b in budgets if b.over_budget]
    return over[-1] if over else None


def budget_left(
    budgets: Sequence[DayBudget],
    upto: int,
    *,
    total_from: Decimal,
    total_to: Decimal,
) -> tuple[Decimal, Decimal]:
    """``B_od`` and ``B_do`` for the days after day ``upto``, after what was spent.

    Args:
        budgets: The days of the trip.
        upto: 1-based index of the last day already behind us.
        total_from: ``B_od`` of the trip.
        total_to: ``B_do`` of the trip.

    Returns:
        ``(B_od, B_do)`` reduced by the spending of the days up to ``upto``;
        ``B_od`` never exceeds ``B_do`` and neither goes below zero.
    """
    spent = sum((b.spent for b in budgets if b.index <= upto), Decimal(0))
    to = max(Decimal(0), total_to - spent)
    return min(to, max(Decimal(0), total_from - spent)), to


def rest_of_trip(
    data: PlanningInput,
    *,
    after: int,
    visited: frozenset[UUID],
    budget_from: Decimal,
    budget_to: Decimal,
) -> PlanningInput:
    """The planning input of the days after day ``after``, with a reduced budget.

    The places of the days behind us are out (a place is visited at most once);
    a "must" that was behind us is dropped with them.

    Args:
        data: The planning input of the whole trip.
        after: 1-based index of the last day already behind us.
        visited: Ids of the places planned up to and including that day.
        budget_from: ``B_od`` left for the rest.
        budget_to: ``B_do`` left for the rest.

    Returns:
        The same people and catalog, the remaining days and the reduced budget.
    """
    trip: PlanningTrip = data.trip.model_copy(
        update={
            "days": data.trip.days[after:],
            "budget_from": budget_from,
            "budget_to": budget_to,
        }
    )
    return data.model_copy(
        update={
            "trip": trip,
            "places": tuple(p for p in data.places if p.id not in visited),
            "must": data.must - visited,
        }
    )
