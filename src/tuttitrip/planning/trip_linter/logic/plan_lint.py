"""A stored plan version as a lint plan (pure)."""

from datetime import date, timedelta
from decimal import Decimal

from tuttitrip.planning.linter.schemas import LintDay, LintItem, LintPlan
from tuttitrip.planning.plans.schemas import PlanDay, PlanRead, PlanStop


def _item(stop: PlanStop, group_size: int) -> LintItem:
    # Prices are per person before the markup: the group total is that times the
    # people, and the linter inflates unverified prices itself (E6).
    base = stop.price_base or Decimal(0)
    return LintItem(
        name=stop.name,
        place_id=stop.place_id,
        start=stop.start,
        end=stop.end if stop.end > stop.start else None,
        cost=base * group_size,
        price_verified=stop.price_verified,
    )


def _date(day: PlanDay, first_day: date) -> date:
    return day.date or first_day + timedelta(days=day.index - 1)


def plan_to_lint(plan: PlanRead, first_day: date) -> LintPlan:
    """Flatten a plan version for the linter.

    Args:
        plan: The stored version.
        first_day: First day of the trip, for a day without a date.

    Returns:
        Days with their stops and the cost of the whole group.
    """
    group_size = plan.fairness.group_size
    return LintPlan(
        days=[
            LintDay(
                day=_date(day, first_day),
                items=[_item(stop, group_size) for stop in day.items],
            )
            for day in plan.days
        ]
    )
