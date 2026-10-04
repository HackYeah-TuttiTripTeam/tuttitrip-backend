"""Cost of a plan, ``c(P)`` (docs/algorytm.md, E6, the cost part).

```
c(P) = sum_places sum_people price (1 + delta [unverified]) + nights * night_price
delta = 0.15
```

Everybody takes part in everything (section 9), so each place is paid by every
person. A person pays the cheapest ticket that applies to their age: ``adult``
always, ``child`` and ``senior`` inside the row's age bounds (defaults: child up
to 17, senior from 65). A row charged per group counts once for the group. A
place without any price row has an unknown price: it adds nothing and is listed
in ``unknown_price_place_ids`` so the caller can warn. Family and student tickets
are not used (no input for them yet). Amounts are ``Decimal`` rounded to cents;
the markup applies only here, the UI shows ``base`` and ``total`` side by side.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from tuttitrip.places.schemas import (
    PlacePriceRead,
    PlaceRead,
    PriceUnit,
    TicketCategory,
)
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.schemas import DayPlan, LodgingStay, PlanningPerson

DEFAULT_CHILD_MAX_AGE = 17
DEFAULT_SENIOR_MIN_AGE = 65
_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class PlanCost:
    """``c(P)`` with the markup and without it."""

    total: Decimal
    """``c(P)``: unverified prices raised by ``delta``."""
    base: Decimal
    """The same sum with the prices as given."""
    unknown_price_place_ids: tuple[UUID, ...]
    """Places in the plan that have no price row."""


def _applies(row: PlacePriceRead, age: int) -> bool:
    match row.ticket_category:
        case TicketCategory.ADULT:
            return True
        case TicketCategory.CHILD:
            low = row.age_min if row.age_min is not None else 0
            high = row.age_max if row.age_max is not None else DEFAULT_CHILD_MAX_AGE
            return low <= age <= high
        case TicketCategory.SENIOR:
            low = row.age_min if row.age_min is not None else DEFAULT_SENIOR_MIN_AGE
            high = row.age_max if row.age_max is not None else 200
            return low <= age <= high
        case _:
            return False


def _charged(row: PlacePriceRead, delta: Decimal) -> Decimal:
    return row.amount * (1 + delta) if not row.verified else row.amount


def _place_cost(
    place: PlaceRead, people: Sequence[PlanningPerson], delta: Decimal
) -> tuple[Decimal, Decimal]:
    # (total, base) for the whole group at one place.
    total = base = Decimal(0)
    group = [r for r in place.prices if r.unit is PriceUnit.GROUP]
    if group:
        cheapest = min(group, key=lambda r: _charged(r, delta))
        total, base = _charged(cheapest, delta), cheapest.amount
    for person in people:
        rows = [
            r
            for r in place.prices
            if r.unit is PriceUnit.PERSON and _applies(r, person.age)
        ]
        if rows:
            row = min(rows, key=lambda r: _charged(r, delta))
            total += _charged(row, delta)
            base += row.amount
    return total, base


def plan_cost(
    days: Sequence[DayPlan],
    places: Mapping[UUID, PlaceRead],
    people: Sequence[PlanningPerson],
    lodging: LodgingStay | None,
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> PlanCost:
    """Cost of a plan for the whole group.

    Args:
        days: The days of the plan.
        places: Places by id.
        people: Everybody on the trip (all pay for every place).
        lodging: The lodging base, or None for a trip without nights.
        params: Algorithm parameters (``unverified_markup``, delta).

    Returns:
        ``c(P)`` with and without the markup, rounded to cents, and the places
        whose price is unknown.
    """
    delta = Decimal(str(params.unverified_markup))
    total = base = Decimal(0)
    unknown: list[UUID] = []
    for day in days:
        for pid in day.place_ids:
            place = places[pid]
            if not place.prices:
                unknown.append(pid)
                continue
            place_total, place_base = _place_cost(place, people, delta)
            total += place_total
            base += place_base
    if lodging is not None:
        stay = lodging.price_per_night * lodging.nights
        total += stay
        base += stay
    return PlanCost(
        total=total.quantize(_CENT, ROUND_HALF_UP),
        base=base.quantize(_CENT, ROUND_HALF_UP),
        unknown_price_place_ids=tuple(unknown),
    )
