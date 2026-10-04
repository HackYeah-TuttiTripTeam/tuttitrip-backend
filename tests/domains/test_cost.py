"""E6 cost of a plan, c(P): hand-computed unit tests."""

from decimal import Decimal
from uuid import uuid4

import pytest

from tests.fixtures.city import places
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.places.schemas import (
    PlacePriceRead,
    PlaceRead,
    PriceUnit,
    TicketCategory,
)
from tuttitrip.planning.logic.cost import PlanCost, place_cost, plan_cost
from tuttitrip.planning.schemas import DayPlan, LodgingStay, PlanningPerson

CATALOG = places()
PEOPLE = planning_input(reference()).people  # Ty 38, Kasia 6, Tomek 13, Babcia 70
TY = PEOPLE[0]
TRIP = planning_input(reference()).trip


def price(  # ruff: ignore[too-many-arguments] test factory
    amount: str,
    *,
    category: TicketCategory = TicketCategory.ADULT,
    unit: PriceUnit = PriceUnit.PERSON,
    verified: bool = True,
    age_min: int | None = None,
    age_max: int | None = None,
    currency: str = "PLN",
) -> PlacePriceRead:
    return (
        CATALOG["muzeum_miejskie"]
        .prices[0]
        .model_copy(
            update={
                "amount": Decimal(amount),
                "ticket_category": category,
                "unit": unit,
                "verified": verified,
                "age_min": age_min,
                "age_max": age_max,
                "currency": currency,
            }
        )
    )


def place(*prices: PlacePriceRead) -> PlaceRead:
    return CATALOG["muzeum_miejskie"].model_copy(
        update={"id": uuid4(), "prices": list(prices)}
    )


def cost(
    p: PlaceRead,
    people: tuple[PlanningPerson, ...] = (TY,),
    lodging: LodgingStay | None = None,
) -> PlanCost:
    trip = TRIP.model_copy(update={"has_lodging": lodging is not None})
    return plan_cost(
        [DayPlan(place_ids=(p.id,))], {p.id: p}, people, trip=trip, lodging=lodging
    )


def test_unverified_price_gets_the_markup() -> None:
    result = cost(place(price("500", verified=False)))
    assert result.total == Decimal("575.00")
    assert result.base == Decimal("500.00")


def test_verified_price_is_unchanged() -> None:
    result = cost(place(price("500")))
    assert result.total == result.base == Decimal("500.00")


def test_everybody_pays_the_cheapest_ticket_for_their_age() -> None:
    museum = place(
        price("30"),
        price("15", category=TicketCategory.CHILD),
        price("20", category=TicketCategory.SENIOR),
    )
    # Ty 30 + Kasia 15 + Tomek 15 + Babcia 20.
    assert cost(museum, PEOPLE).total == Decimal("80.00")


def test_concession_bounds_override_the_defaults() -> None:
    museum = place(price("30"), price("10", category=TicketCategory.CHILD, age_max=12))
    kasia, tomek = PEOPLE[1], PEOPLE[2]  # 6 and 13
    assert cost(museum, (kasia, tomek)).total == Decimal("40.00")  # 10 + 30


def test_group_price_is_charged_once() -> None:
    boat = place(price("100", unit=PriceUnit.GROUP))
    assert cost(boat, PEOPLE).total == Decimal("100.00")


def test_free_and_unknown_prices() -> None:
    free = place(price("0"))
    assert cost(free, PEOPLE).total == Decimal("0.00")
    unknown = place()
    result = cost(unknown, PEOPLE)
    assert result.total == Decimal("0.00")
    assert result.unknown_price_place_ids == (unknown.id,)


def test_lodging_nights_are_added_without_markup() -> None:
    result = cost(
        place(price("500", verified=False)),
        lodging=LodgingStay(nights=2, price_per_night=Decimal(300)),
    )
    assert result.total == Decimal("1175.00")
    assert result.base == Decimal("1100.00")


def test_the_same_place_on_two_days_is_paid_twice() -> None:
    p = place(price("50"))
    days = [DayPlan(place_ids=(p.id,)), DayPlan(place_ids=(p.id,))]
    trip = TRIP.model_copy(update={"has_lodging": False})
    assert plan_cost(days, {p.id: p}, (TY,), trip=trip, lodging=None).total == Decimal(
        "100.00"
    )


def test_group_and_person_rows_charge_the_cheaper_not_both() -> None:
    boat = place(price("100", unit=PriceUnit.GROUP), price("30"))
    # Four people at 30 = 120 > the group ticket 100.
    assert cost(boat, PEOPLE).total == Decimal("100.00")
    # One person at 30 < 100.
    assert cost(boat, (TY,)).total == Decimal("30.00")


def test_group_row_covers_people_nobody_else_prices() -> None:
    child_only = place(
        price("10", category=TicketCategory.CHILD), price("90", unit=PriceUnit.GROUP)
    )
    result = cost(child_only, PEOPLE)
    assert result.total == Decimal("90.00")
    assert result.unknown_price_place_ids == ()


def test_family_and_student_rows_are_not_used() -> None:
    zoo = place(
        price("30"),
        price("80", category=TicketCategory.FAMILY),
        price("5", category=TicketCategory.STUDENT),
    )
    assert cost(zoo, PEOPLE).total == Decimal("120.00")


def test_a_person_without_an_applicable_row_makes_the_place_unknown() -> None:
    child_only = place(price("10", category=TicketCategory.CHILD))
    result = cost(child_only, PEOPLE)  # Kasia and Tomek are priced, Ty and Babcia not
    assert result.total == Decimal("20.00")
    assert result.unknown_price_place_ids == (child_only.id,)


def test_night_only_and_foreign_currency_rows_are_unknown() -> None:
    night = place(price("300", unit=PriceUnit.NIGHT))
    euro = place(price("20", currency="EUR"))
    for p in (night, euro):
        result = cost(p)
        assert result.total == Decimal("0.00")
        assert result.unknown_price_place_ids == (p.id,)


def test_ticket_ties_are_broken_deterministically() -> None:
    # 100 verified vs 87 unverified: 87 * 1.15 = 100.05 > 100, so verified wins.
    verified_first = place(price("100"), price("87", verified=False))
    reversed_ = place(price("87", verified=False), price("100"))
    assert cost(verified_first).total == Decimal("100.00")
    assert cost(reversed_).total == Decimal("100.00")
    # Equal charged amount: 100 unverified is 115, 115 verified is 115; the lower
    # listed amount (the unverified 100) wins, base 100.
    tie = place(price("115"), price("100", verified=False))
    assert cost(tie).base == Decimal("100.00")


def test_cents_round_half_up() -> None:
    # 2.30 * 1.15 = 2.645 -> 2.65 (banker's rounding would give 2.64).
    assert cost(place(price("2.30", verified=False))).total == Decimal("2.65")


def test_place_cost_is_public_and_matches_the_plan() -> None:
    museum = place(price("30"), price("15", category=TicketCategory.CHILD))
    single = place_cost(museum, PEOPLE, "PLN")
    assert single.total == Decimal(90)
    assert single.complete
    assert cost(museum, PEOPLE).total == single.total


def test_lodging_must_agree_with_the_trip() -> None:
    p = place(price("10"))
    with_nights = TRIP.model_copy(update={"has_lodging": True})
    with pytest.raises(ValueError, match="nights"):
        plan_cost(
            [DayPlan(place_ids=(p.id,))],
            {p.id: p},
            (TY,),
            trip=with_nights,
            lodging=None,
        )
    without = TRIP.model_copy(update={"has_lodging": False})
    stay = LodgingStay(nights=1, price_per_night=Decimal(100))
    with pytest.raises(ValueError, match="nights"):
        plan_cost(
            [DayPlan(place_ids=(p.id,))], {p.id: p}, (TY,), trip=without, lodging=stay
        )
