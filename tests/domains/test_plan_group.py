"""E4 (reference point u*, r, effective floor) and the group computation: tests."""

from decimal import Decimal
from uuid import uuid5

import pytest

from tests.fixtures.expected import REFERENCE_PEOPLE
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import all_scenarios, reference, solo
from tuttitrip.planning.logic.plan_group import GroupPlan, plan_group
from tuttitrip.planning.logic.reference import (
    relative_satisfaction,
    solo_input,
    solo_lodging,
    solo_utility,
)
from tuttitrip.planning.logic.solver import solve
from tuttitrip.planning.schemas import LodgingStay, PlanningInput
from tuttitrip.profiles.preferences.schemas import ImportanceDomain

PRICE_PER_NIGHT = Decimal(250)


def lodging_for(key: str, days: int) -> LodgingStay | None:
    nights = days - 1
    if key == "solo" or not nights:
        return None
    return LodgingStay(nights=nights, price_per_night=PRICE_PER_NIGHT)


def run(key: str) -> tuple[PlanningInput, GroupPlan]:
    scenario = all_scenarios()[key]
    stay = lodging_for(key, scenario.days)
    data = planning_input(scenario, lodging=stay is not None)
    return data, plan_group(data, lodging=stay)


# --- r and the effective floor ------------------------------------------------------


def test_relative_satisfaction_hand_computed() -> None:
    # (51.4 + 10) / (60.3 + 10) = 0.8734, "87%" in the table of section 7.
    assert relative_satisfaction(51.4, 60.3) == pytest.approx(61.4 / 70.3)
    assert relative_satisfaction(70, 60) == pytest.approx(1)  # capped at 1
    assert relative_satisfaction(0, 0) == pytest.approx(1)


@pytest.mark.parametrize("key", REFERENCE_PEOPLE)
def test_r_matches_the_percentages_of_the_spec_table(key: str) -> None:
    row = REFERENCE_PEOPLE[key]
    r = relative_satisfaction(row.u, row.u_star)
    assert round(100 * r) == row.r_percent


def test_solo_input_gives_one_person_a_share_of_the_budget() -> None:
    data = planning_input(reference())
    alone = solo_input(data, data.people[1])
    assert alone.people == (data.people[1],)
    assert alone.trip.budget_from == data.trip.budget_from / 4
    assert alone.trip.budget_to == data.trip.budget_to / 4
    assert alone.trip.budget_max == data.trip.budget_max / 4
    assert not alone.must
    stay = LodgingStay(nights=2, price_per_night=Decimal(400))
    share = solo_lodging(stay, 4)
    assert share is not None
    assert share.price_per_night == Decimal(100)
    assert solo_lodging(None, 4) is None


# --- n = 1 ----------------------------------------------------------------------------


def test_for_one_person_r_is_one_and_the_floor_is_zero() -> None:
    data, result = run("solo")
    (row,) = result.people
    assert row.r == pytest.approx(1)
    assert row.floor_eff == pytest.approx(0)
    assert row.u_star == pytest.approx(row.u)
    assert row.floor_met
    assert result.jain == pytest.approx(1)
    assert result.min_r == pytest.approx(1)
    assert result.solo_runs == 0
    assert row.weakest_domain in ImportanceDomain
    # The plan is the solver's plan for that person (no separate path).
    assert (
        result.plan.plan_hash == solve(data, floors={data.people[0].id: 0.0}).plan_hash
    )


def test_weakest_domain_is_the_lowest_applicable_score() -> None:
    _, result = run("solo")
    scores = result.plan.scores[0]
    row = result.people[0]
    values = {
        ImportanceDomain.FOOD: scores.food,
        ImportanceDomain.ATTRACTIONS: scores.attractions,
        ImportanceDomain.PACE: scores.pace,
        ImportanceDomain.COST: scores.cost,
    }
    assert row.weakest_domain is not None
    assert values[row.weakest_domain] == min(values.values())


# --- groups -------------------------------------------------------------------------


@pytest.mark.parametrize("key", [k for k in all_scenarios() if k != "solo"])
def test_nobody_gets_more_than_alone_and_floors_are_capped(key: str) -> None:
    data, result = run(key)
    assert result.solo_runs == len(data.people)
    missed = set(result.plan.report.floors_missed)
    for row in result.people:
        assert row.u <= row.u_star + 1e-6
        assert row.floor_eff <= 0.6 * row.u_star + 1e-9
        assert row.floor_eff <= row.floor + 1e-9
        assert 0 < row.r <= 1
        assert row.floor_met or row.person_id in missed
        assert row.weakest_domain is None
    assert 0 < result.min_r <= 1
    assert 1 / len(data.people) <= result.jain <= 1


def test_ledger_rows_are_in_id_order_and_match_the_plan() -> None:
    _, result = run("reference")
    ids = [str(r.person_id) for r in result.people]
    assert ids == sorted(ids)
    by_person = {s.person_id: s.welfare for s in result.plan.scores}
    assert all(r.u == by_person[r.person_id] for r in result.people)
    assert result.min_r == min(r.r for r in result.people)


def test_the_result_does_not_depend_on_the_order_of_people() -> None:
    data, first = run("reference")
    stay = lodging_for("reference", 3)
    reordered = data.model_copy(update={"people": tuple(reversed(data.people))})
    second = plan_group(reordered, lodging=stay)
    assert second.plan.plan_hash == first.plan.plan_hash
    assert [r.u_star for r in second.people] == [r.u_star for r in first.people]


def test_solo_run_matches_solve_for_that_person() -> None:
    data = planning_input(reference())
    stay = LodgingStay(nights=2, price_per_night=PRICE_PER_NIGHT)
    person = data.people[2]
    run_ = solo_utility(data, person, lodging=stay)
    direct = solve(
        solo_input(data, person),
        lodging=solo_lodging(stay, 4),
        floors={person.id: 0.0},
    )
    assert run_.plan_hash == direct.plan_hash


def test_a_heavier_weight_does_not_lower_that_persons_welfare() -> None:
    data, base = run("reference")
    stay = lodging_for("reference", 3)
    target = data.people[0]
    heavier = tuple(
        p.model_copy(update={"weight": p.weight * 2}) if p.id == target.id else p
        for p in data.people
    )
    # Weights stay within 3x: Ty 2 against Kasia and Tomek 2, Babcia 1.
    after = plan_group(data.model_copy(update={"people": heavier}), lodging=stay)
    before_u = next(r.u for r in base.people if r.person_id == target.id)
    after_u = next(r.u for r in after.people if r.person_id == target.id)
    assert after_u >= before_u - 1e-6


def test_clones_with_a_proportional_budget_get_the_solo_plan() -> None:
    single = planning_input(solo(), lodging=False)
    alone = plan_group(single)
    n = 3
    clones = tuple(
        single.people[0].model_copy(
            update={"id": uuid5(single.people[0].id, f"clone-{i}")}
        )
        for i in range(n)
    )
    trip = single.trip.model_copy(
        update={
            "budget_from": single.trip.budget_from * n,
            "budget_to": single.trip.budget_to * n,
        }
    )
    group = plan_group(single.model_copy(update={"people": clones, "trip": trip}))

    def per_day(result: GroupPlan) -> list[tuple[str, ...]]:
        return [
            tuple(str(v.place_id) for v in d.schedule.visits) for d in result.plan.days
        ]

    assert per_day(group) == per_day(alone)
    assert group.plan.plan_hash == alone.plan.plan_hash  # section 4, test 5
    assert {round(r.u, 4) for r in group.people} == {round(alone.people[0].u, 4)}
    assert {round(r.r, 6) for r in group.people} == {1.0}
