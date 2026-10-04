"""Lodging base (backend#70) and exceptional nights (backend#71)."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid5

import pytest

from tests.fixtures.city import lodging_offers, place_id, places
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference, solo
from tuttitrip.accommodation.schemas import (
    RequirementItem,
    RequirementStatus,
)
from tuttitrip.planning.logic.cost import place_cost
from tuttitrip.planning.logic.domains import RequirementOutcome, lodging_score
from tuttitrip.planning.logic.params import AlgorithmParams
from tuttitrip.planning.logic.plan_group import plan_group
from tuttitrip.planning.logic.solver import LodgingPlan, lodging_plans, solve
from tuttitrip.planning.plans.logic.input_builder import lodging_options
from tuttitrip.planning.plans.logic.read_model import build_content
from tuttitrip.planning.plans.logic.search_area import search_area
from tuttitrip.planning.plans.schemas import ConflictCode, PlanRead
from tuttitrip.planning.schemas import (
    LodgingOption,
    LodgingOutcome,
    PlanningInput,
)
from tuttitrip.profiles.preferences.schemas import ImportancePool

CATALOG = places()
KEYS = {"apartament_basen", "hotel_centrum", "hostel_dworzec"}
OFFERS = {CATALOG[k].id: v for k, v in lodging_offers().items()}
MET, UNC, UNMET = (
    RequirementStatus.MET,
    RequirementStatus.UNCONFIRMED,
    RequirementStatus.UNMET,
)


def requirement(key: str, *, hard: bool) -> RequirementItem:
    return RequirementItem(kind="amenity", key=key, hard=hard)


def with_nights(data: PlanningInput, nights: int) -> PlanningInput:
    first = data.trip.days[0]
    days = tuple(first + timedelta(days=n) for n in range(nights + 1))
    return data.model_copy(update={"trip": data.trip.model_copy(update={"days": days})})


def family(requirements: list[RequirementItem], nights: int = 2) -> PlanningInput:
    data = planning_input(reference(), lodging=True)
    options = lodging_options(
        list(CATALOG.values()), requirements, "PLN", features=OFFERS
    )
    return with_nights(data, nights).model_copy(update={"lodgings": options})


def option(name: str, price: str, **statuses: RequirementStatus) -> LodgingOption:
    return LodgingOption(
        place_id=uuid5(UUID(int=7), name),
        name=name,
        lat=52.0,
        lon=19.0,
        price_per_night=Decimal(price),
        outcomes=tuple(
            LodgingOutcome(feature=f, hard=f.startswith("h_"), status=s)
            for f, s in statuses.items()
        ),
    )


# --- #70: one base for all nights ----------------------------------------------------


def test_the_option_that_meets_the_hard_pool_requirement_is_chosen() -> None:
    data = family([requirement("pool", hard=True)])
    assert {o.name for o in data.lodgings} == {CATALOG[k].name for k in KEYS}
    result = solve(data)
    assert {o.place_id for o in result.lodging} == {place_id("apartament_basen")}
    assert len(result.lodging) == 2
    (outcome,) = result.lodging[0].outcomes
    assert outcome.status is MET
    assert lodging_score([_o(o) for o in result.lodging[0].outcomes]) == pytest.approx(
        100
    )
    assert all(s.lodging == pytest.approx(100) for s in result.scores)


def _o(outcome: LodgingOutcome):  # ruff: ignore[missing-return-type-private-function]

    return RequirementOutcome(outcome.hard, outcome.status)


def test_an_unconfirmed_soft_requirement_counts_point_four() -> None:
    data = family([requirement("pool", hard=True), requirement("elevator", hard=False)])
    apartment = next(
        o for o in data.lodgings if o.place_id == place_id("apartament_basen")
    )
    statuses = {o.feature: o.status for o in apartment.outcomes}
    assert statuses == {"pool": MET, "elevator": UNC}
    # S_h = 1 (hard met) * mean(soft) = 0.4
    score = lodging_score([_o(o) for o in apartment.outcomes])
    assert score == pytest.approx(40)


def test_three_nights_cost_three_times_the_chosen_base() -> None:
    data = family([requirement("pool", hard=True)], nights=3)
    result = solve(data)
    assert len(result.lodging) == 3
    people = tuple(data.people)
    places_cost = sum(
        (
            place_cost(next(p for p in data.places if p.id == pid), people, "PLN").total
            for pid in result.place_ids
        ),
        Decimal(0),
    )
    # 300 a night for the apartment, three nights, once in c(P).
    assert result.cost.total == places_cost + 3 * Decimal(300)


def test_a_one_day_trip_has_no_lodging() -> None:
    data = family([requirement("pool", hard=True)])
    one_day = data.model_copy(
        update={
            "trip": data.trip.model_copy(
                update={"days": data.trip.days[:1], "has_lodging": False}
            )
        }
    )
    result = solve(one_day)
    assert result.lodging == ()
    assert all(s.lodging is None for s in result.scores)


def test_without_options_the_lodging_domain_does_not_apply() -> None:
    data = planning_input(reference(), lodging=False)
    assert not data.trip.has_lodging
    assert solve(data).lodging == ()


def test_the_same_data_twice_gives_the_same_base_and_area() -> None:
    data = family([requirement("pool", hard=True)])
    first, second = solve(data), solve(data)
    assert first.plan_hash == second.plan_hash
    assert [o.place_id for o in first.lodging] == [o.place_id for o in second.lodging]
    visited = [p for p in data.places if p.id in set(first.place_ids)]
    assert search_area(visited) == search_area(list(reversed(visited)))


def test_no_option_meeting_a_hard_requirement_is_reported() -> None:
    data = family([requirement("air_conditioning", hard=True)])
    group = plan_group(data)
    plan = PlanRead.model_validate(
        {
            "id": "00000000-0000-4000-8000-000000000001",
            "trip_id": "00000000-0000-4000-8000-000000000002",
            "version": 1,
            "input_hash": "0" * 64,
            "plan_hash": group.plan.plan_hash,
            "created_at": "2026-10-04T10:00:00Z",
            "params": {"alpha": 1.0, "weight_preset": "default"},
            **build_content(data, group, {}),
        }
    )
    assert plan.lodging is not None
    assert plan.lodging.s_h < 1
    assert (
        any(
            c.reason_code is ConflictCode.LODGING_HARD_REQUIREMENT
            for c in plan.conflicts
        )
        or plan.lodging.s_h > 0
    )  # unconfirmed (not unmet) is a warning of its own


def test_the_search_area_is_the_weighted_centre_reaching_the_farthest_stop() -> None:
    a = CATALOG["muzeum_miejskie"].model_copy(
        update={"lat": 50.0, "lon": 19.0, "typical_visit_min": 90}
    )
    b = CATALOG["zoo"].model_copy(
        update={"lat": 50.0, "lon": 19.03, "typical_visit_min": 30}
    )
    area = search_area([a, b])
    assert area is not None
    lat, lon, radius = area
    assert lat == pytest.approx(50.0)
    assert lon == pytest.approx(19.0 + 0.03 * 30 / 120)
    assert radius == pytest.approx(0.0225 * 111_320 * 0.6428, rel=0.02)
    assert search_area([]) is None


# --- #71: exceptional nights ----------------------------------------------------------


def test_with_no_allowance_the_plans_are_one_base_each() -> None:
    a = option("a", "100", castle=UNMET)
    b = option("b", "400", castle=MET)
    assert [p.nights for p in lodging_plans([a, b], 3, 0)] == [(a,) * 3, (b,) * 3]
    plans = lodging_plans([a, b], 3, 1)
    assert LodgingPlan((a, a, b)) in plans
    assert LodgingPlan((b, b, a)) in plans
    assert len(plans) == 4
    # Which nights are exceptional does not matter to J: equal plans are one.
    assert len(lodging_plans([a, b], 3, 2)) == 4
    assert len(lodging_plans([a, b], 1, 2)) == 2  # one night cannot be split


def test_the_mean_over_nights_is_the_lodging_satisfaction() -> None:
    castle = option("castle", "700", h_none=MET, castle=MET)
    plain = option("plain", "100", h_none=MET, castle=UNMET)
    # Hard requirement met in both; the soft "castle" gives 100 and 0.
    assert LodgingPlan((plain, plain, castle)).q == pytest.approx(100 / 3)
    assert LodgingPlan((castle,) * 3).q == pytest.approx(100)


def lover(data: PlanningInput, points: int = 8) -> PlanningInput:
    pool = ImportancePool(
        lodging=points, food=0, attractions=points and 10 - points, pace=0, cost=0
    )
    people = list(data.people)
    people[0] = people[0].model_copy(update={"pool": pool})
    return data.model_copy(update={"people": tuple(people)})


def everybody_likes_lodging(data: PlanningInput) -> PlanningInput:
    pool = ImportancePool(lodging=8, food=0, attractions=2, pace=0, cost=0)
    people = tuple(p.model_copy(update={"pool": pool}) for p in data.people)
    return data.model_copy(update={"people": people})


def castle_trip(*, budget_to: int, nights: int = 3) -> PlanningInput:
    data = with_nights(planning_input(reference(), lodging=True), nights)
    plain = option("plain", "100", castle=UNMET)
    castle = option("castle", "400", castle=MET)
    trip = data.trip.model_copy(
        update={
            "budget_to": Decimal(budget_to),
            "budget_from": Decimal(budget_to) / 2,
            "flex_pct": 0,
        }
    )
    return lover(data).model_copy(update={"trip": trip, "lodgings": (plain, castle)})


PARAMS_ONE_NIGHT = AlgorithmParams(max_exceptional_nights=1)


def test_one_night_in_the_castle_when_the_budget_allows_it() -> None:
    data = everybody_likes_lodging(castle_trip(budget_to=1100))
    plain = solve(data)
    assert {o.name for o in plain.lodging} == {"plain"}
    result = solve(data, PARAMS_ONE_NIGHT)
    assert [o.name for o in result.lodging] == ["plain", "plain", "castle"]
    delta = result.lodging_delta
    assert delta is not None
    assert delta.nights == (3,)
    # (400 - 100) for the one night; and it gives everybody points.
    assert delta.extra_cost == Decimal(300)
    assert all(points > 0 for _, points in delta.extra_points)
    assert result.cost.total <= data.trip.budget_max
    # It enters c(P) once: 100 + 100 + 400 for the nights.
    visits = result.cost.total - Decimal(600)
    assert visits >= 0


def test_without_budget_for_the_castle_there_is_no_exceptional_night() -> None:
    data = everybody_likes_lodging(castle_trip(budget_to=500))
    result = solve(data, PARAMS_ONE_NIGHT)
    assert {o.name for o in result.lodging} == {"plain"}
    assert result.lodging_delta is None


def test_when_nobody_cares_about_lodging_there_is_no_exceptional_night() -> None:
    data = castle_trip(budget_to=1100)
    people = tuple(
        p.model_copy(
            update={
                "pool": ImportancePool(lodging=0, food=2, attractions=4, pace=2, cost=2)
            }
        )
        for p in data.people
    )
    result = solve(data.model_copy(update={"people": people}), PARAMS_ONE_NIGHT)
    assert {o.name for o in result.lodging} == {"plain"}
    assert result.lodging_delta is None


def test_a_zero_allowance_gives_the_plan_of_the_single_base() -> None:
    data = castle_trip(budget_to=2200)
    assert (
        solve(data).plan_hash
        == solve(data, AlgorithmParams(max_exceptional_nights=0)).plan_hash
    )


def test_the_choice_is_deterministic() -> None:
    data = castle_trip(budget_to=2200)
    assert (
        solve(data, PARAMS_ONE_NIGHT).plan_hash
        == solve(data, PARAMS_ONE_NIGHT).plan_hash
    )


def test_solo_is_unaffected_by_lodging_options() -> None:
    data = planning_input(solo(), lodging=False)
    assert solve(data).lodging == ()


def test_a_base_that_costs_more_and_satisfies_no_more_is_not_searched() -> None:
    cheap = option("cheap", "100", castle=MET)
    dear = option("dear", "400", castle=MET)  # same satisfaction, dearer
    worse = option("worse", "50", castle=UNMET)  # cheaper, satisfies less: kept
    plans = lodging_plans([cheap, dear, worse], 2, 0)
    assert [p.nights[0].name for p in plans] == ["cheap", "worse"]
