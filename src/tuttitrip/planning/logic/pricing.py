"""Public transport tickets for the plan, shown as information (backend#54).

Not part of ``c(P)`` and not part of the budget (docs/algorytm.md, E6 counts only
entry prices and nights): the plan shows what getting around costs. Entry prices
with concessions and family tickets are in ``cost.py``.

For each kind of passenger (adult, child, senior by age) the cheapest cover of the
trip is chosen from the city's tariff: a single ticket per ride, a 24-hour ticket
per day with rides, 72-hour tickets or weekly tickets over the span from the first
day with a ride to the last one. A kind of passenger without a fare of its own
pays the adult fare. A city without an adult fare has no known price:
``total`` is None and ``verified`` is False. Family transit tickets are not
modelled (the tariff has no family size). Pure, standard library only.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from operator import itemgetter

from tuttitrip.places.schemas import TransitFareRead
from tuttitrip.planning.logic.cost import DEFAULT_CHILD_MAX_AGE, DEFAULT_SENIOR_MIN_AGE
from tuttitrip.planning.schemas import PlanningPerson

SINGLE = "single"
DAY = "24h"
THREE_DAYS = "72h"
WEEK = "weekly"
_BLOCKS = ((THREE_DAYS, 3), (WEEK, 7))


@dataclass(frozen=True, slots=True)
class TransitTicket:
    """What one kind of passenger buys."""

    category: str
    ticket_type: str
    count: int
    """Tickets per person (rides for singles, days or blocks otherwise)."""
    people: int
    cost: Decimal
    """For all people of the category."""


@dataclass(frozen=True, slots=True)
class TransitCost:
    """Cost of getting around, for information."""

    total: Decimal | None
    tickets: tuple[TransitTicket, ...]
    verified: bool
    """Every fare used comes from a source."""
    source_url: str | None
    rides_per_day: tuple[int, ...]


def category_of(person: PlanningPerson) -> str:
    """The fare category of a person by age.

    Args:
        person: The person.

    Returns:
        ``child`` up to 17, ``senior`` from 65, else ``adult``.
    """
    if person.age <= DEFAULT_CHILD_MAX_AGE:
        return "child"
    if person.age >= DEFAULT_SENIOR_MIN_AGE:
        return "senior"
    return "adult"


def _options(
    fares: dict[str, TransitFareRead], rides: Sequence[int]
) -> list[tuple[Decimal, str, int, TransitFareRead]]:
    # (cost per person, ticket type, count, fare) for every way to cover the rides.
    options: list[tuple[Decimal, str, int, TransitFareRead]] = []
    if SINGLE in fares:
        count = sum(rides)
        options.append((fares[SINGLE].amount * count, SINGLE, count, fares[SINGLE]))
    riding = [i for i, n in enumerate(rides) if n > 0]
    if DAY in fares:
        options.append((fares[DAY].amount * len(riding), DAY, len(riding), fares[DAY]))
    if riding:
        span = riding[-1] - riding[0] + 1
        for ticket_type, days in _BLOCKS:
            if ticket_type in fares:
                blocks = -(-span // days)
                options.append(
                    (
                        fares[ticket_type].amount * blocks,
                        ticket_type,
                        blocks,
                        fares[ticket_type],
                    )
                )
    return options


def transit_cost(
    rides_per_day: Sequence[int],
    people: Sequence[PlanningPerson],
    fares: Sequence[TransitFareRead],
    currency: str,
) -> TransitCost:
    """The cheapest tickets for the trip's rides.

    Args:
        rides_per_day: Rides between the stops of each day (stops minus one).
        people: Everybody on the trip.
        fares: The city's tariff.
        currency: Trip currency; fares in another currency are ignored.

    Returns:
        The tickets, their total and whether every fare is verified.
    """
    by_category: dict[str, dict[str, TransitFareRead]] = {}
    for fare in fares:
        if fare.currency == currency:
            by_category.setdefault(fare.person_category, {})[fare.ticket_type] = fare
    rides = tuple(rides_per_day)
    adult = by_category.get("adult")
    if not adult or not any(rides):
        return TransitCost(
            total=None if not adult else Decimal(0),
            tickets=(),
            verified=bool(adult) and not any(rides),
            source_url=None,
            rides_per_day=rides,
        )
    tickets: list[TransitTicket] = []
    verified = True
    source: str | None = None
    for category, count in sorted(Counter(category_of(p) for p in people).items()):
        options = _options(by_category.get(category) or adult, rides)
        if not options:
            continue
        cost, ticket_type, number, fare = min(options, key=itemgetter(0, 1))
        tickets.append(
            TransitTicket(category, ticket_type, number, count, cost * count)
        )
        verified = verified and fare.verified
        source = source or fare.source_url
    total = sum((t.cost for t in tickets), Decimal(0))
    return TransitCost(
        total=total if tickets else None,
        tickets=tuple(tickets),
        verified=verified and bool(tickets),
        source_url=source,
        rides_per_day=rides,
    )


@dataclass(frozen=True, slots=True)
class DayTicket:
    """A ticket bought on a day, for the whole group."""

    day: int
    """1-based day of the plan."""
    ticket: str
    """``single``, ``day``, ``h72`` or ``week``."""
    cost: Decimal
    verified: bool
    source_url: str | None


_KIND = {SINGLE: "single", DAY: "day", THREE_DAYS: "h72", WEEK: "week"}


def day_tickets(
    rides_per_day: Sequence[int],
    people: Sequence[PlanningPerson],
    fares: Sequence[TransitFareRead],
    currency: str,
) -> list[DayTicket]:
    """The cheapest tickets by the day they are bought.

    A single ticket appears on each day with rides (the cost of that day's rides),
    a 24-hour ticket on each day with rides, a 72-hour or weekly ticket on the
    first day of each block it covers. The cost is for the whole group; kinds of
    passenger that buy the same ticket on a day are added up.

    Args:
        rides_per_day: Rides between the stops of each day.
        people: Everybody on the trip.
        fares: The city's tariff.
        currency: Trip currency.

    Returns:
        The tickets by day; empty without a tariff or without rides.
    """
    cost = transit_cost(rides_per_day, people, fares, currency)
    rides = tuple(rides_per_day)
    riding = [i for i, n in enumerate(rides) if n > 0]
    found: dict[tuple[int, str], DayTicket] = {}
    by_category: dict[str, dict[str, TransitFareRead]] = {}
    for fare in fares:
        if fare.currency == currency:
            by_category.setdefault(fare.person_category, {})[fare.ticket_type] = fare

    def add(day: int, kind: str, amount: Decimal, fare: TransitFareRead) -> None:
        key = (day + 1, _KIND[kind])
        old = found.get(key)
        found[key] = DayTicket(
            day + 1,
            _KIND[kind],
            amount + (old.cost if old else Decimal(0)),
            fare.verified and (old.verified if old else True),
            (old.source_url if old else None) or fare.source_url,
        )

    for ticket in cost.tickets:
        fare = (by_category.get(ticket.category) or by_category["adult"])[
            ticket.ticket_type
        ]
        each = fare.amount * ticket.people
        if ticket.ticket_type == SINGLE:
            for day in riding:
                add(day, SINGLE, each * rides[day], fare)
        elif ticket.ticket_type == DAY:
            for day in riding:
                add(day, DAY, each, fare)
        else:
            length = dict(_BLOCKS)[ticket.ticket_type]
            for block in range(ticket.count):
                add(riding[0] + block * length, ticket.ticket_type, each, fare)
    return [found[key] for key in sorted(found)]
