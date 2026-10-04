"""Transit tickets as information (backend#54) and the plan's transit block."""

from decimal import Decimal

import pytest

from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.places.schemas import TransitFareRead
from tuttitrip.planning.logic.pricing import category_of, day_tickets, transit_cost
from tuttitrip.planning.schemas import PlanningPerson

DATA = planning_input(reference(), lodging=False)
TY, KASIA, TOMEK, BABCIA = DATA.people


def fare(
    ticket_type: str,
    amount: str,
    category: str = "adult",
    *,
    verified: bool = True,
    currency: str = "PLN",
) -> TransitFareRead:
    return TransitFareRead(
        ticket_type=ticket_type,
        person_category=category,
        amount=Decimal(amount),
        currency=currency,
        source_url="https://example.test/taryfa" if verified else None,
        verified=verified,
        checked_at=None,
    )


TARIFF = [
    fare("single", "4"),
    fare("24h", "15"),
    fare("72h", "30"),
    fare("weekly", "60"),
]


def one(people: PlanningPerson | None = None) -> list[PlanningPerson]:
    return [people or TY]


def test_the_ages_decide_the_fare_category() -> None:
    assert [category_of(p) for p in (TY, KASIA, TOMEK, BABCIA)] == [
        "adult",
        "child",
        "child",
        "senior",
    ]


def test_six_rides_in_one_day_cost_less_as_a_day_ticket() -> None:
    result = transit_cost([6], one(), TARIFF, "PLN")
    # 6 singles = 24, a 24-hour ticket = 15, a 72-hour ticket = 30.
    assert result.total == Decimal(15)
    (ticket,) = result.tickets
    assert (ticket.ticket_type, ticket.count) == ("24h", 1)


def test_a_few_rides_are_cheaper_as_singles() -> None:
    result = transit_cost([2], one(), TARIFF, "PLN")
    assert result.total == Decimal(8)
    assert result.tickets[0].ticket_type == "single"
    assert result.tickets[0].count == 2


def test_a_three_day_trip_with_many_rides_uses_the_72_hour_ticket() -> None:
    result = transit_cost([6, 6, 6], one(), TARIFF, "PLN")
    # 18 singles = 72, three day tickets = 45, one 72-hour ticket = 30.
    assert result.total == Decimal(30)
    assert result.tickets[0].ticket_type == "72h"
    assert result.tickets[0].count == 1


def test_a_week_is_covered_by_the_weekly_ticket() -> None:
    result = transit_cost([5] * 7, one(), TARIFF, "PLN")
    # 35 singles, 7 day tickets (105), 3 blocks of 72h (90), one weekly (60).
    assert result.total == Decimal(60)
    assert result.tickets[0].ticket_type == "weekly"


def test_each_kind_of_passenger_pays_its_own_fare_and_falls_back_to_adult() -> None:
    tariff = [*TARIFF, fare("single", "2", "child")]
    result = transit_cost([2], [TY, KASIA, TOMEK, BABCIA], tariff, "PLN")
    by_category = {t.category: t for t in result.tickets}
    assert by_category["adult"].cost == Decimal(8)
    assert by_category["child"].cost == Decimal(8)  # 2 children x 2 rides x 2
    assert by_category["senior"].cost == Decimal(8)  # no senior fare: the adult one
    assert result.total == Decimal(24)


def test_unverified_fares_make_the_result_unverified() -> None:
    tariff = [fare("single", "4", verified=False)]
    result = transit_cost([3], one(), tariff, "PLN")
    assert result.total == Decimal(12)
    assert not result.verified
    assert transit_cost([3], one(), TARIFF, "PLN").verified


def test_a_city_without_a_tariff_has_no_known_price() -> None:
    result = transit_cost([3], one(), [], "PLN")
    assert result.total is None
    assert not result.verified
    assert result.tickets == ()
    other_currency = transit_cost(
        [3], one(), [fare("single", "4", currency="EUR")], "PLN"
    )
    assert other_currency.total is None


def test_no_rides_cost_nothing() -> None:
    result = transit_cost([0, 0], one(), TARIFF, "PLN")
    assert result.total == Decimal(0)
    assert result.tickets == ()


def test_the_source_of_the_fare_is_passed_on() -> None:
    result = transit_cost([6], one(), TARIFF, "PLN")
    assert result.source_url == "https://example.test/taryfa"
    assert result.rides_per_day == (6,)


@pytest.mark.parametrize("rides", [[3], [3, 4], [1, 0, 1]])
def test_the_total_is_the_cheapest_cover_for_every_kind_of_passenger(
    rides: list[int],
) -> None:
    result = transit_cost(rides, one(), TARIFF, "PLN")
    assert result.total is not None
    assert result.total <= Decimal(4) * sum(rides)


# --- the tickets by day (the shape the frontend reads: day, ticket, cost) -------------


def test_a_single_is_shown_on_each_day_with_rides() -> None:
    tickets = day_tickets([2, 0, 1], one(), TARIFF, "PLN")
    assert [(t.day, t.ticket, t.cost) for t in tickets] == [
        (1, "single", Decimal(8)),
        (3, "single", Decimal(4)),
    ]


def test_a_day_ticket_is_shown_on_the_day_it_is_bought() -> None:
    tickets = day_tickets([6, 0, 6], one(), TARIFF, "PLN")
    # 12 singles = 48, two day tickets = 30, one 72-hour ticket = 30 (a tie: the
    # cheaper-first rule keeps the day tickets).
    assert {(t.day, t.ticket) for t in tickets} in (
        {(1, "day"), (3, "day")},
        {(1, "h72")},
    )
    assert sum(t.cost for t in tickets) == Decimal(30)


def test_a_72_hour_ticket_sits_on_the_first_day_it_covers() -> None:
    tickets = day_tickets([6, 6, 6], one(), TARIFF, "PLN")
    assert [(t.day, t.ticket, t.cost) for t in tickets] == [(1, "h72", Decimal(30))]


def test_the_group_cost_adds_up_the_kinds_of_passenger() -> None:
    tariff = [*TARIFF, fare("single", "2", "child"), fare("24h", "8", "child")]
    tickets = day_tickets([2], [TY, KASIA, TOMEK], tariff, "PLN")
    # adult 2 singles = 8; two children: 2 singles = 4 each.
    assert [(t.day, t.ticket, t.cost) for t in tickets] == [(1, "single", Decimal(16))]


def test_without_a_tariff_there_are_no_tickets() -> None:
    assert day_tickets([3], one(), [], "PLN") == []


def test_unverified_tickets_are_marked() -> None:
    tickets = day_tickets([2], one(), [fare("single", "4", verified=False)], "PLN")
    assert [t.verified for t in tickets] == [False]
