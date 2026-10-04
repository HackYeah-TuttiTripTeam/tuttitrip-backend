"""Cost of a plan, ``c(P)`` (docs/algorytm.md, E6, the cost part).

```
c(P) = sum_places sum_people price (1 + delta [unverified]) + nights * night_price
delta = 0.15
```

Everybody takes part in everything (section 9), so each place is paid by every
person. A person pays the cheapest ticket that applies to their age: ``adult``
always, ``child`` and ``senior`` inside the row's age bounds (defaults: child up
to 17, senior from 65). A ``group`` row covers the whole group for any size; a
place with both kinds charges the cheaper of the group ticket and the sum of the
person tickets. A ``family`` ticket (``family_size`` people, default 4) is used
when it is cheaper for the group than the person tickets: ``ceil(n / size)`` of
them, and the cost per person is that total divided equally (a product decision).
``student`` rows are not used (no student flag in the input), and rows in another
currency or charged per night are ignored. Ties between
tickets are broken by ``(charged amount, listed amount)``. Amounts are
``Decimal`` rounded half up to cents; the markup applies only here, the UI shows
``base`` and ``total`` side by side.

Unknown prices. A place with no usable price (no rows, or none that applies to
somebody) adds nothing for what it cannot price and is listed in
``unknown_price_place_ids``. Zero is not a promise: **a plan with any unknown
price carries a warning (``has_unpriced_places``), and its ``c(P) <= B_max``
check is not proven.** The solver (backend#47) must not favour
unpriced places by treating them as free.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from operator import itemgetter
from uuid import UUID

from tuttitrip.places.schemas import (
    PlacePriceRead,
    PlaceRead,
    PriceUnit,
    TicketCategory,
)
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.schemas import (
    DayPlan,
    LodgingStay,
    PlanningPerson,
    PlanningTrip,
)

DEFAULT_CHILD_MAX_AGE = 17
DEFAULT_SENIOR_MIN_AGE = 65
DEFAULT_FAMILY_SIZE = 4
"""Family ticket without ``family_size``: 2 adults and 2 children."""
NO_UPPER_AGE = 200
"""Upper age bound of a senior row without ``age_max``."""
_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class PersonPrice:
    """What one person pays at a place, after the unverified-price markup."""

    person_id: UUID
    price: Decimal
    discount: str
    """``none``, ``child``, ``senior``, ``student``, ``free`` or ``family``."""


@dataclass(frozen=True, slots=True)
class PlaceCost:
    """What a group pays at one place."""

    total: Decimal
    """With the markup on unverified prices."""
    base: Decimal
    """The same with the prices as given."""
    complete: bool
    """False when somebody (or everybody) could not be priced."""
    lines: tuple[PersonPrice, ...] = ()
    """The price of each person who could be priced."""
    family_singles_total: Decimal | None = None
    """When a family ticket was chosen: what the group pays with single tickets."""


@dataclass(frozen=True, slots=True)
class PlanCost:
    """``c(P)`` with the markup and without it."""

    total: Decimal
    """``c(P)``: unverified prices raised by ``delta``."""
    base: Decimal
    """The same sum with the prices as given."""
    unknown_price_place_ids: tuple[UUID, ...]
    """Places not fully priced; the plan needs approval and a warning."""


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
            high = row.age_max if row.age_max is not None else NO_UPPER_AGE
            return low <= age <= high
        case TicketCategory.FAMILY | TicketCategory.STUDENT:
            return False  # not used: no family size or student flag in the input
        case TicketCategory.REDUCED:
            # "ulgowa" from the sheet does not say for whom (child, senior, student):
            # applying it would guess. A place with only this row is unpriced.
            return False


def _charged(row: PlacePriceRead, delta: Decimal) -> Decimal:
    return row.amount if row.verified else row.amount * (1 + delta)


def _cheapest(rows: Sequence[PlacePriceRead], delta: Decimal) -> PlacePriceRead:
    return min(rows, key=lambda r: (_charged(r, delta), r.amount))


def place_cost(
    place: PlaceRead,
    people: Sequence[PlanningPerson],
    currency: str,
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> PlaceCost:
    """What the whole group pays at one place (for the solver's precompute).

    Args:
        place: The place.
        people: Everybody on the trip.
        currency: Trip currency; rows in another currency are ignored.
        params: Algorithm parameters (``unverified_markup``, delta).

    Returns:
        Total with the markup, base without it, and whether everybody could be
        priced. Not complete means the plan carries an unknown price.
    """
    delta = Decimal(str(params.unverified_markup))
    rows = [r for r in place.prices if r.currency == currency]
    family_rows = [
        r
        for r in rows
        if r.ticket_category is TicketCategory.FAMILY and r.unit is not PriceUnit.NIGHT
    ]
    group_rows = [
        r
        for r in rows
        if r.unit is PriceUnit.GROUP and r.ticket_category is not TicketCategory.FAMILY
    ]
    person_rows = [r for r in rows if r.unit is PriceUnit.PERSON]

    total, base, complete, lines = _by_person(person_rows, people, delta)
    # Other ways to pay for everybody at once: a group ticket, family tickets.
    alternatives: list[tuple[Decimal, Decimal, str]] = []
    if group_rows:
        group = _cheapest(group_rows, delta)
        alternatives.append((_charged(group, delta), group.amount, "none"))
    if family_rows:
        best = min(
            ((_family_total(r, len(people), delta), r) for r in family_rows),
            key=lambda pair: (pair[0], pair[1].amount),
        )
        alternatives.append(
            (best[0], best[1].amount * _family_count(best[1], len(people)), "family")
        )
    if alternatives:
        cheapest = min(alternatives, key=itemgetter(0, 1))
        if not complete or cheapest[0] < total:
            share = cheapest[0] / len(people)
            return PlaceCost(
                cheapest[0],
                cheapest[1],
                complete=True,
                lines=tuple(PersonPrice(p.id, share, cheapest[2]) for p in people),
                family_singles_total=total
                if cheapest[2] == "family" and complete
                else None,
            )
    return PlaceCost(total, base, complete=complete, lines=tuple(lines))


def _by_person(
    person_rows: Sequence[PlacePriceRead],
    people: Sequence[PlanningPerson],
    delta: Decimal,
) -> tuple[Decimal, Decimal, bool, list[PersonPrice]]:
    # Each person pays the cheapest ticket of their age; (total, base, complete, lines).
    total = base = Decimal(0)
    lines: list[PersonPrice] = []
    for person in people:
        fitting = [r for r in person_rows if _applies(r, person.age)]
        if not fitting:
            continue
        row = _cheapest(fitting, delta)
        total += _charged(row, delta)
        base += row.amount
        lines.append(PersonPrice(person.id, _charged(row, delta), _discount(row)))
    return total, base, len(lines) == len(people), lines


def _discount(row: PlacePriceRead) -> str:
    if row.amount == 0:
        return "free"
    match row.ticket_category:
        case TicketCategory.CHILD:
            return "child"
        case TicketCategory.SENIOR:
            return "senior"
        case TicketCategory.STUDENT:
            return "student"
        case _:
            return "none"


def _family_count(row: PlacePriceRead, people: int) -> int:
    # Family tickets needed to cover everybody (2+2 = 4 people unless the row says).
    size = row.family_size or DEFAULT_FAMILY_SIZE
    return -(-people // size)


def _family_total(row: PlacePriceRead, people: int, delta: Decimal) -> Decimal:
    return _charged(row, delta) * _family_count(row, people)


def plan_cost(  # ruff: ignore[too-many-arguments] the whole input of E6
    days: Sequence[DayPlan],
    places: Mapping[UUID, PlaceRead],
    people: Sequence[PlanningPerson],
    *,
    trip: PlanningTrip,
    lodging: LodgingStay | Decimal | None,
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> PlanCost:
    """Cost of a plan for the whole group.

    Args:
        days: The days of the plan.
        places: Places by id.
        people: Everybody on the trip (all pay for every place).
        trip: Gives the currency and whether the trip has nights.
        lodging: The lodging base, or its whole cost over all nights; given
            exactly when ``trip.has_lodging``.
        params: Algorithm parameters (``unverified_markup``, delta).

    Returns:
        ``c(P)`` with and without the markup, rounded half up to cents, and the
        places that are not fully priced.

    Raises:
        ValueError: When ``lodging`` and ``trip.has_lodging`` disagree.
    """
    if (lodging is not None) != trip.has_lodging:
        msg = "lodging must be given exactly when the trip has nights"
        raise ValueError(msg)
    total = base = Decimal(0)
    unknown: list[UUID] = []
    for day in days:
        for pid in day.place_ids:
            cost = place_cost(places[pid], people, trip.currency, params)
            total += cost.total
            base += cost.base
            if not cost.complete:
                unknown.append(pid)
    if lodging is not None:
        stay = (
            lodging
            if isinstance(lodging, Decimal)
            else lodging.price_per_night * lodging.nights
        )
        total += stay
        base += stay
    return PlanCost(
        total=total.quantize(_CENT, ROUND_HALF_UP),
        base=base.quantize(_CENT, ROUND_HALF_UP),
        unknown_price_place_ids=tuple(unknown),
    )
