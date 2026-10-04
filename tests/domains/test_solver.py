"""Solver of the group goal J (docs/algorytm.md, section 9): property-style tests."""

import itertools
import subprocess  # ruff: ignore[suspicious-subprocess-import] fixed snippet
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from tests.fixtures.city import key_of, place_id, places
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import Scenario, all_scenarios, reference
from tuttitrip.planning.logic.hard_constraints import filter_places
from tuttitrip.planning.logic.schedule import DAILY_KM_FACTOR
from tuttitrip.planning.logic.solver import (
    PlanEvaluator,
    PlanResult,
    SolverConflict,
    solve,
)
from tuttitrip.planning.schemas import LodgingStay, PlanningInput

CATALOG = places()
ROOT = Path(__file__).resolve().parents[2]
PRICE_PER_NIGHT = Decimal(250)


def lodging_for(scenario: Scenario) -> LodgingStay | None:
    nights = scenario.days - 1
    return (
        LodgingStay(nights=nights, price_per_night=PRICE_PER_NIGHT) if nights else None
    )


def run(scenario: Scenario) -> tuple[PlanningInput, PlanResult]:
    lodging = lodging_for(scenario) if scenario.key != "solo" else None
    data = planning_input(scenario, lodging=lodging is not None)
    return data, solve(data, lodging=lodging)


def ids_of(result: PlanResult) -> list[UUID]:
    return list(result.place_ids)


# --- hard constraints on every fixture scenario ----------------------------------


@pytest.mark.parametrize("key", list(all_scenarios()))
def test_plan_respects_the_hard_constraints(key: str) -> None:
    data, result = run(all_scenarios()[key])
    chosen = ids_of(result)
    accepted = {p.id for p in filter_places(data).accepted}
    assert len(chosen) == len(set(chosen))  # every place at most once
    assert set(chosen) <= accepted  # E0: no veto, stairs, segment, closed place
    assert result.cost.total <= data.trip.budget_max
    for day in result.days:
        for person in data.people:
            limit = DAILY_KM_FACTOR * person.daily_km
            assert day.schedule.distance_km <= limit
    assert result.telemetry.evaluations > 0
    assert not result.conflicts


@pytest.mark.parametrize("key", list(all_scenarios()))
def test_every_person_gets_a_score_and_the_plan_is_not_empty(key: str) -> None:
    data, result = run(all_scenarios()[key])
    assert len(result.scores) == len(data.people)
    assert result.place_ids
    assert all(0 <= s.welfare <= 100 for s in result.scores)
    assert result.objective.value == pytest.approx(
        result.objective.welfare - 1000 * result.objective.violation
    )


def test_veto_is_never_in_the_plan_and_must_is() -> None:
    data = planning_input(reference())
    babcia = data.people[3]
    assert place_id("restauracja_morska") in babcia.vetoes
    must = place_id("hevelianum")
    result = solve(
        data.model_copy(update={"must": frozenset({must})}),
        lodging=LodgingStay(nights=2, price_per_night=PRICE_PER_NIGHT),
    )
    assert must in ids_of(result)
    assert place_id("restauracja_morska") not in ids_of(result)
    assert result.cost.total <= data.trip.budget_max


def test_a_new_veto_removes_the_place_from_the_plan() -> None:
    scenario = reference()
    data, before = run(scenario)
    victim = ids_of(before)[0]
    people = (
        data.people[0].model_copy(update={"vetoes": frozenset({victim})}),
        *data.people[1:],
    )
    after = solve(
        data.model_copy(update={"people": people}), lodging=lodging_for(scenario)
    )
    assert victim in ids_of(before)
    assert victim not in ids_of(after)


def test_rejected_and_unplaceable_musts_are_reported() -> None:
    data = planning_input(reference())
    sea = place_id("restauracja_morska")  # vetoed
    stay = LodgingStay(nights=2, price_per_night=PRICE_PER_NIGHT)
    result = solve(data.model_copy(update={"must": frozenset({sea})}), lodging=stay)
    assert (SolverConflict.MUST_REJECTED, sea) in result.conflicts
    assert sea not in ids_of(result)
    tight = solve(
        data.model_copy(update={"must": frozenset({place_id("hevelianum")})}),
        lodging=stay,
        cost_cap=Decimal(500),
    )
    assert (SolverConflict.MUST_UNPLACEABLE, place_id("hevelianum")) in tight.conflicts
    assert tight.cost.total <= Decimal(500)


# --- the budget cap -----------------------------------------------------------------


def test_cost_cap_is_a_hard_limit_and_a_plan_always_exists() -> None:
    scenario = reference()
    stay = lodging_for(scenario)
    data = planning_input(scenario)
    for cap in (Decimal(2000), Decimal(900), Decimal(600)):
        result = solve(data, lodging=stay, cost_cap=cap)
        assert result.cost.total <= cap
    # Lodging alone costs 500; a cap of 100 allows nothing that costs money, but
    # there is still a plan (free places only) and the conflict is reported.
    empty = solve(data, lodging=stay, cost_cap=Decimal(100))
    assert empty.cost.total == Decimal(500)
    assert (SolverConflict.LODGING_OVER_CAP, None) in empty.conflicts


def test_unknown_prices_need_approval() -> None:
    data = planning_input(reference())
    stay = LodgingStay(nights=2, price_per_night=PRICE_PER_NIGHT)
    priced = [p for p in data.places if p.prices]
    only_priced = data.model_copy(update={"places": tuple(priced)})
    assert not solve(only_priced, lodging=stay).needs_approval
    unpriced = next(p for p in data.places if not p.prices)
    must = data.model_copy(update={"must": frozenset({unpriced.id})})
    result = solve(must, lodging=stay)
    assert unpriced.id in result.cost.unknown_price_place_ids
    assert result.needs_approval


# --- soft guarantees never block the plan ---------------------------------------


def test_an_impossible_floor_still_gives_a_plan_and_is_reported() -> None:
    data, _ = run(reference())
    kasia = data.people[1]
    people = (
        data.people[0],
        kasia.model_copy(update={"floor": 100.0}),
        *data.people[2:],
    )
    result = solve(
        data.model_copy(update={"people": people}),
        lodging=LodgingStay(nights=2, price_per_night=PRICE_PER_NIGHT),
    )
    assert result.place_ids
    assert kasia.id in result.report.floors_missed
    assert result.objective.violation > 0


def test_a_one_day_trip_without_lodging_needs_no_special_code() -> None:
    data = planning_input(reference(), lodging=False)
    one_day = data.model_copy(
        update={"trip": data.trip.model_copy(update={"days": data.trip.days[:1]})}
    )
    result = solve(one_day)
    assert len(result.days) == 1
    assert result.place_ids
    assert all(s.lodging is None for s in result.scores)


def test_the_work_limit_counts_evaluations_and_still_returns_a_plan() -> None:
    data, _ = run(reference())
    result = solve(
        data,
        lodging=LodgingStay(nights=2, price_per_night=PRICE_PER_NIGHT),
        max_evaluations=1,
    )
    assert result.telemetry.evaluations <= 2
    assert result.days


def test_lodging_must_match_the_trip() -> None:
    data = planning_input(reference())
    with pytest.raises(ValueError, match="nights"):
        solve(data)


# --- agreement with brute force on small instances ----------------------------------

SMALL = (
    "muzeum_miejskie",
    "zoo",
    "bar_mleczny",
    "restauracja_indyjska",
    "park_oliwski",
)


def small_instance(people: slice) -> PlanningInput:
    data = planning_input(reference(), lodging=False)
    keep = {place_id(k) for k in SMALL}
    return data.model_copy(
        update={
            "people": data.people[people],
            "places": tuple(p for p in data.places if p.id in keep),
            "trip": data.trip.model_copy(update={"days": data.trip.days[:2]}),
        }
    )


@pytest.mark.parametrize("who", [slice(0, 1), slice(0, 4)], ids=["solo", "group"])
def test_solver_matches_brute_force(who: slice) -> None:
    data = small_instance(who)
    result = solve(data)
    evaluator = PlanEvaluator(data)
    pool = [p.id for p in evaluator.candidates]
    best: float | None = None
    for slots in itertools.product(range(3), repeat=len(pool)):
        assignment = tuple(
            tuple(
                sorted(
                    (p for p, s in zip(pool, slots, strict=True) if s == d + 1), key=str
                )
            )
            for d in range(2)
        )
        evaluation = evaluator.evaluate(assignment)
        if evaluation and (best is None or evaluation.objective.value > best):
            best = evaluation.objective.value
    assert best is not None
    assert result.objective.value >= best - 0.005 * abs(best)


def test_for_one_person_the_weight_does_not_change_the_plan() -> None:
    data = small_instance(slice(0, 1))
    hashes = {
        solve(
            data.model_copy(
                update={"people": (data.people[0].model_copy(update={"weight": w}),)}
            )
        ).plan_hash
        for w in (1.0, 2.0, 3.0)
    }
    assert len(hashes) == 1


# --- determinism --------------------------------------------------------------------


def test_input_order_does_not_change_the_plan() -> None:
    scenario = reference()
    data = planning_input(scenario)
    stay = lodging_for(scenario)
    baseline = solve(data, lodging=stay)
    shuffled = data.model_copy(
        update={
            "people": tuple(reversed(data.people)),
            "places": tuple(sorted(data.places, key=lambda p: key_of(p)[::-1])),
        }
    )
    again = solve(shuffled, lodging=stay)
    assert again.plan_hash == baseline.plan_hash
    assert again.place_ids == baseline.place_ids
    assert solve(data, lodging=stay).plan_hash == baseline.plan_hash


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


def test_hash_is_the_same_in_processes_with_different_hash_seeds() -> None:
    first, second = hash_in_process("1"), hash_in_process("4242")
    assert len(first) == 12
    assert first == second
    _, here = run(reference())
    assert first == here.plan_hash
