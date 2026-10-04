"""Section 8 of docs/algorytm.md as pytest: properties of the planning algorithm.

The five groups of the specification, run on the fixtures of backend#38 with no
model. The reference implementation's own 26 cases are not in the repo yet
(backend#152); when they arrive each test here maps onto one of them. Small
instances are checked against brute force, larger ones for properties.
"""

import math
import subprocess  # ruff: ignore[suspicious-subprocess-import] fixed snippet
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from tests.fixtures.brute_force import best
from tests.fixtures.city import lodging_offers, place_id, places
from tests.fixtures.expected import (
    REFERENCE_JAIN,
    REFERENCE_MIN_R,
    REFERENCE_PEOPLE,
)
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import (
    Scenario,
    all_scenarios,
    friends_scenario,
    reference,
    solo,
)
from tuttitrip.accommodation.logic.contract import evaluate
from tuttitrip.accommodation.schemas import Requirement, RequirementStatus
from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.fairness.logic.measure import jain, min_r
from tuttitrip.planning.fairness.logic.violations import (
    effective_floor,
    min_tag_count,
)
from tuttitrip.planning.fairness.logic.welfare import welfare
from tuttitrip.planning.logic.cost import place_cost
from tuttitrip.planning.logic.domains import RequirementOutcome, lodging_score
from tuttitrip.planning.logic.plan_group import GroupPlan, plan_group
from tuttitrip.planning.logic.reference import relative_satisfaction
from tuttitrip.planning.logic.schedule import DAILY_KM_FACTOR
from tuttitrip.planning.logic.solver import PlanEvaluator, PlanResult, solve
from tuttitrip.planning.logic.welfare_person import welfare as person_welfare
from tuttitrip.planning.schemas import LodgingStay, PlanningInput
from tuttitrip.profiles.preferences.schemas import ImportanceDomain

ROOT = Path(__file__).resolve().parents[2]
CATALOG = places()
NIGHT = Decimal(250)
SMALL = (
    "muzeum_miejskie",
    "zoo",
    "bar_mleczny",
    "restauracja_indyjska",
    "park_oliwski",
)
PERMUTATIONS = [(2, 0, 3, 1), (3, 2, 1, 0), (1, 3, 0, 2)]


def stay_for(scenario: Scenario) -> LodgingStay | None:
    nights = scenario.days - 1
    if scenario.key == "solo" or not nights:
        return None
    return LodgingStay(nights=nights, price_per_night=NIGHT)


def instance(scenario: Scenario) -> tuple[PlanningInput, LodgingStay | None]:
    stay = stay_for(scenario)
    return planning_input(scenario, lodging=stay is not None), stay


def small(people: slice) -> PlanningInput:
    data = planning_input(reference(), lodging=False)
    keep = {place_id(k) for k in SMALL}
    return data.model_copy(
        update={
            "people": data.people[people],
            "places": tuple(p for p in data.places if p.id in keep),
            "trip": data.trip.model_copy(update={"days": data.trip.days[:2]}),
        }
    )


def assignment_of(result: PlanResult) -> tuple[tuple[UUID, ...], ...]:
    return tuple(
        tuple(sorted((v.place_id for v in d.schedule.visits), key=str))
        for d in result.days
    )


def schedule_of(result: PlanResult) -> list[tuple[str, ...]]:
    return [tuple(str(v.place_id) for v in d.schedule.visits) for d in result.days]


# === Group 1: reduction to n = 1 =====================================================


def test_solo_solver_matches_brute_force() -> None:
    data = small(slice(0, 1))
    evaluator = PlanEvaluator(data)
    optimum = best(evaluator, 2).objective.value
    assert solve(data).objective.value >= optimum - 0.005 * abs(optimum)


def test_solo_maximises_the_persons_own_utility() -> None:
    data = small(slice(0, 1))
    evaluator = PlanEvaluator(data)
    top = best(evaluator, 2).scores[0].welfare
    assert solve(data).scores[0].welfare >= top - 0.005 * top


def test_solo_has_r_one_and_floor_zero() -> None:
    result = plan_group(planning_input(solo(), lodging=False))
    (row,) = result.people
    assert row.r == pytest.approx(1)
    assert row.floor_eff == pytest.approx(0)
    assert jain([row.r]) == pytest.approx(1)


def test_solo_weight_changes_nothing() -> None:
    data = small(slice(0, 1))
    hashes = {
        solve(
            data.model_copy(
                update={"people": (data.people[0].model_copy(update={"weight": w}),)}
            )
        ).plan_hash
        for w in (1.0, 2.0, 3.0)
    }
    assert len(hashes) == 1


def test_clones_with_a_proportional_budget_equal_solo() -> None:
    single = planning_input(solo(), lodging=False)
    alone = plan_group(single)
    n = 3
    clones = tuple(
        single.people[0].model_copy(update={"id": uuid5(single.people[0].id, str(i))})
        for i in range(n)
    )
    trip = single.trip.model_copy(
        update={
            "budget_from": single.trip.budget_from * n,
            "budget_to": single.trip.budget_to * n,
        }
    )
    group = plan_group(single.model_copy(update={"people": clones, "trip": trip}))
    assert schedule_of(group.plan) == schedule_of(alone.plan)
    assert {round(r.u, 4) for r in group.people} == {round(alone.people[0].u, 4)}


# === Group 2: the group ==============================================================


def test_group_solver_matches_brute_force() -> None:
    data = small(slice(0, 4))
    optimum = best(PlanEvaluator(data), 2).objective.value
    assert solve(data).objective.value >= optimum - 0.005 * abs(optimum)


def test_same_input_gives_the_same_plan_every_time() -> None:
    data, stay = instance(reference())
    assert solve(data, lodging=stay).plan_hash == solve(data, lodging=stay).plan_hash


SNIPPET = """
from decimal import Decimal
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.planning.logic.solver import solve
from tuttitrip.planning.schemas import LodgingStay
data = planning_input(reference())
data = data.model_copy(update={"people": tuple(reversed(data.people))})
stay = LodgingStay(nights=2, price_per_night=Decimal(250))
print(solve(data, lodging=stay).plan_hash)
"""


def hash_in_process(seed: str) -> str:
    done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] fixed interpreter and snippet
        [sys.executable, "-c", SNIPPET],
        cwd=ROOT,
        env={"PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT), "PATH": ""},
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_hash_is_equal_across_processes_with_different_hash_seeds() -> None:
    data, stay = instance(reference())
    here = solve(data, lodging=stay).plan_hash
    assert hash_in_process("1") == hash_in_process("987654") == here


def test_order_of_people_and_places_does_not_matter() -> None:
    data, stay = instance(reference())
    baseline = solve(data, lodging=stay)
    for order in PERMUTATIONS:
        shuffled = data.model_copy(
            update={
                "people": tuple(data.people[i] for i in order),
                "places": tuple(reversed(data.places)),
            }
        )
        assert solve(shuffled, lodging=stay).plan_hash == baseline.plan_hash


@pytest.mark.parametrize("key", [k for k in all_scenarios() if k != "solo"])
def test_floors_are_met_or_reported_and_never_above_sixty_percent(key: str) -> None:
    scenario = all_scenarios()[key]
    data, stay = instance(scenario)
    result = plan_group(data, lodging=stay)
    missed = set(result.plan.report.floors_missed)
    for row in result.people:
        assert row.floor_eff <= 0.6 * row.u_star + 1e-9
        assert row.floor_eff == pytest.approx(effective_floor(row.floor, row.u_star))
        assert row.floor_met or row.person_id in missed


@pytest.mark.parametrize("key", [k for k in all_scenarios() if k != "solo"])
def test_nobody_gets_more_than_alone(key: str) -> None:
    data, stay = instance(all_scenarios()[key])
    result = plan_group(data, lodging=stay)
    assert all(row.u <= row.u_star + 1e-6 for row in result.people)


# === Group 3: hard constraints =======================================================


def test_veto_is_never_in_the_plan() -> None:
    data, stay = instance(reference())
    result = solve(data, lodging=stay)
    assert place_id("restauracja_morska") in data.people[3].vetoes
    assert place_id("restauracja_morska") not in result.place_ids


def test_must_is_in_the_plan() -> None:
    data, stay = instance(reference())
    must = place_id("hevelianum")
    result = solve(data.model_copy(update={"must": frozenset({must})}), lodging=stay)
    assert must in result.place_ids


@pytest.mark.parametrize("key", list(all_scenarios()))
def test_budget_and_daily_distance_hold_on_every_scenario(key: str) -> None:
    data, stay = instance(all_scenarios()[key])
    result = solve(data, lodging=stay)
    assert result.cost.total <= data.trip.budget_max
    for day in result.days:
        for person in data.people:
            assert day.schedule.distance_km <= DAILY_KM_FACTOR * person.daily_km


def test_a_one_day_trip_without_lodging_needs_no_special_code() -> None:
    data = planning_input(reference(), lodging=False)
    one_day = data.model_copy(
        update={"trip": data.trip.model_copy(update={"days": data.trip.days[:1]})}
    )
    result = solve(one_day)
    assert len(result.days) == 1
    assert result.place_ids
    assert all(s.lodging is None for s in result.scores)


# === Group 4: fairness ===============================================================


def test_a_heavier_weight_does_not_lower_that_persons_welfare() -> None:
    data, stay = instance(friends_scenario())
    for target in data.people:
        before = plan_group(data, lodging=stay)
        heavier = tuple(
            p.model_copy(update={"weight": p.weight * 2}) if p.id == target.id else p
            for p in data.people
        )
        after = plan_group(data.model_copy(update={"people": heavier}), lodging=stay)
        u_before = next(r.u for r in before.people if r.person_id == target.id)
        u_after = next(r.u for r in after.people if r.person_id == target.id)
        assert u_after >= u_before - 1e-6


def test_the_plan_for_each_alpha_maximises_its_own_objective() -> None:
    data, stay = instance(friends_scenario())
    alphas = (0.0, 1.0, 3.0)
    plans = {a: solve(data, alpha=a, lodging=stay) for a in alphas}
    for own in alphas:
        evaluator = PlanEvaluator(data, alpha=own, lodging=stay)
        values = {
            other: evaluation.objective.value
            for other, plan in plans.items()
            if (evaluation := evaluator.evaluate(assignment_of(plan)))
        }
        assert values[own] >= max(values.values()) - 0.005 * abs(values[own])


@pytest.mark.parametrize("alpha", [0.5, 1.0, 2.0, 3.0])
def test_transfer_from_better_to_worse_off_raises_welfare(alpha: float) -> None:
    assert welfare([(55, 1), (45, 1)], alpha) > welfare([(90, 1), (10, 1)], alpha)


def test_balanced_domains_beat_a_lopsided_plan() -> None:
    data = planning_input(reference())
    person = data.people[0].model_copy(
        update={
            "pool": data.people[0].pool.model_copy(
                update={"lodging": 0, "food": 0, "attractions": 5, "pace": 5, "cost": 0}
            )
        }
    )
    lopsided = dict.fromkeys(ImportanceDomain, 0.0) | {
        ImportanceDomain.ATTRACTIONS: 100.0
    }
    even = dict.fromkeys(ImportanceDomain, 0.0) | {
        ImportanceDomain.ATTRACTIONS: 50.0,
        ImportanceDomain.PACE: 50.0,
    }
    assert person_welfare(person, lopsided, has_lodging=True) == pytest.approx(
        math.sqrt(101) - 1
    )
    assert person_welfare(person, even, has_lodging=True) == pytest.approx(50)


def test_a_pool_minimum_puts_the_tag_into_the_plan() -> None:
    data, stay = instance(reference())
    tomek = data.people[2]
    assert tomek.pool.food == 4
    assert min_tag_count(tomek.pool.food) == 1
    result = solve(data, lodging=stay)
    assert place_id("restauracja_indyjska") in result.place_ids
    assert not result.report.conflicts or all(
        c.tag != "indian" for c in result.report.conflicts
    )


# === Group 5: data ===================================================================


def test_unverified_price_raises_the_cost_by_delta() -> None:
    data = planning_input(reference(), lodging=False)
    museum: PlaceRead = CATALOG["muzeum_miejskie"]
    adult_only = museum.model_copy(update={"prices": [museum.prices[0]]})
    verified = place_cost(adult_only, data.people[:1], "PLN")
    row = adult_only.prices[0].model_copy(update={"verified": False})
    unverified = place_cost(
        adult_only.model_copy(update={"prices": [row]}), data.people[:1], "PLN"
    )
    assert unverified.total == verified.total * Decimal("1.15")
    assert unverified.base == verified.base


def test_lodging_contract_states() -> None:
    offers = lodging_offers()

    def score(offer: str) -> float:
        outcomes = [
            RequirementOutcome(
                hard=hard,
                status=evaluate(Requirement(feature=feature), offers[offer]).status,
            )
            for feature, hard in (("pool", True), ("elevator", False))
        ]
        return lodging_score(outcomes)

    assert evaluate(
        Requirement(feature="elevator"), offers["apartament_basen"]
    ).status is (RequirementStatus.UNCONFIRMED)
    assert score("apartament_basen") == pytest.approx(40)  # pool met, elevator 0.4
    assert score("hotel_centrum") == pytest.approx(0)  # no pool: hard unmet
    assert score("hostel_dworzec") == pytest.approx(0)


def test_jain_index_and_min_r_match_the_spec_table() -> None:
    r = [relative_satisfaction(row.u, row.u_star) for row in REFERENCE_PEOPLE.values()]
    assert jain(r) == pytest.approx(REFERENCE_JAIN, abs=0.001)
    assert min_r(r) == pytest.approx(REFERENCE_MIN_R, abs=0.005)
    assert jain([1.0, 1.0]) == pytest.approx(1)
    assert jain([1.0, 0.0]) == pytest.approx(0.5)


def test_every_fixture_scenario_produces_a_plan_without_a_model() -> None:
    for scenario in all_scenarios().values():
        data, stay = instance(scenario)
        result: GroupPlan = plan_group(data, lodging=stay)
        assert result.plan.place_ids
