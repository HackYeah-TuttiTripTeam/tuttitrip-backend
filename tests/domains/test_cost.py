"""E6 cost of a plan, c(P): hand-computed unit tests."""

from decimal import Decimal
from uuid import uuid4

from tests.fixtures.city import places
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.places.schemas import (
    PlacePriceRead,
    PlaceRead,
    PriceUnit,
    TicketCategory,
)
from tuttitrip.planning.logic.cost import PlanCost, plan_cost
from tuttitrip.planning.schemas import DayPlan, LodgingStay, PlanningPerson

CATALOG = places()
PEOPLE = planning_input(reference()).people  # Ty 38, Kasia 6, Tomek 13, Babcia 70
TY = PEOPLE[0]


def price(  # ruff: ignore[too-many-arguments] test factory
    amount: str,
    *,
    category: TicketCategory = TicketCategory.ADULT,
    unit: PriceUnit = PriceUnit.PERSON,
    verified: bool = True,
    age_min: int | None = None,
    age_max: int | None = None,
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
    return plan_cost([DayPlan(place_ids=(p.id,))], {p.id: p}, people, lodging)


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
    assert plan_cost(days, {p.id: p}, (TY,), None).total == Decimal("100.00")
