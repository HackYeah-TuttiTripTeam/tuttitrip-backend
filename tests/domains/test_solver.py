"""Solver of the group goal J (docs/algorytm.md, section 9): property-style tests."""

import itertools
import subprocess  # ruff: ignore[suspicious-subprocess-import] fixed snippet
import sys
from datetime import time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from tests.fixtures.brute_force import best
from tests.fixtures.city import key_of, place_id, places
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import Scenario, all_scenarios, reference, solo
from tuttitrip.places.schemas import OpeningHours, PlaceRead, TimeRange, Weekday
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
    assert not solve(only_priced, lodging=stay).has_unpriced_places
    unpriced = next(p for p in data.places if not p.prices)
    must = data.model_copy(update={"must": frozenset({unpriced.id})})
    result = solve(must, lodging=stay)
    assert unpriced.id in result.cost.unknown_price_place_ids
    assert result.has_unpriced_places


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


# --- musts, limits, larger brute force, independent checks ------------------------


def single_day_place(
    base_key: str, tag: str, *, hours_days: list[Weekday] | None = None
) -> PlaceRead:
    """A copy of a catalog place with a new id, 1 km of walking, optional days."""
    base = CATALOG[base_key]
    hours = base.hours
    if hours_days is not None:
        weekly = {d: [TimeRange(open="10:00", close="18:00")] for d in hours_days}
        hours = hours.model_copy(update={"opening_hours": OpeningHours(weekly=weekly)})
    return base.model_copy(
        update={
            "id": uuid5(base.id, tag),
            "segment_km": 1.0,
            "stairs": 0.0,
            "hours": hours,
        }
    )


@pytest.mark.parametrize("tag", ["a", "b", "c", "d", "e", "f"])
def test_a_must_is_tried_on_other_days_so_that_both_musts_fit(tag: str) -> None:
    # Two 1 km stops exceed 1.5 * 1 km a day. B is open only on Friday (day 1);
    # A, placed first on its best day (also day 1), would block it.
    # The tags give different ids, so both orders of the two musts are exercised.
    a = single_day_place("zoo", tag)
    b = single_day_place("hevelianum", tag, hours_days=[Weekday.FRI])
    data = planning_input(reference(), lodging=False)
    walker = data.people[0].model_copy(update={"daily_km": 1.0, "segment_km": 3.0})
    tight = data.model_copy(
        update={
            "people": (walker,),
            "places": (a, b),
            "must": frozenset({a.id, b.id}),
        }
    )
    result = solve(tight)
    assert {a.id, b.id} <= set(result.place_ids)
    kinds = [kind for kind, _ in result.conflicts]
    assert SolverConflict.MUST_UNPLACEABLE not in kinds
    day_of = {
        v.place_id: i for i, d in enumerate(result.days) for v in d.schedule.visits
    }
    assert day_of[b.id] == 0
    assert day_of[a.id] != day_of[b.id]


def test_an_unplaceable_must_is_reported_from_the_final_plan() -> None:
    a = single_day_place("zoo", "x", hours_days=[Weekday.FRI])
    b = single_day_place("hevelianum", "x", hours_days=[Weekday.FRI])
    data = planning_input(reference(), lodging=False)
    walker = data.people[0].model_copy(update={"daily_km": 1.0, "segment_km": 3.0})
    both_friday = data.model_copy(
        update={
            "people": (walker,),
            "places": (a, b),
            "must": frozenset({a.id, b.id}),
        }
    )
    result = solve(both_friday)
    unplaced = {
        pid for kind, pid in result.conflicts if kind is SolverConflict.MUST_UNPLACEABLE
    }
    assert len(unplaced) == 1
    assert unplaced.isdisjoint(result.place_ids)


def test_the_work_limit_sets_exhausted_and_stays_deterministic() -> None:
    data, stay = instance_for(reference())
    first = solve(data, lodging=stay, max_evaluations=30)
    second = solve(data, lodging=stay, max_evaluations=30)
    assert first.telemetry.exhausted
    assert first.plan_hash == second.plan_hash
    assert not solve(data, lodging=stay).telemetry.exhausted


def instance_for(scenario: Scenario) -> tuple[PlanningInput, LodgingStay | None]:
    stay = lodging_for(scenario) if scenario.key != "solo" else None
    return planning_input(scenario, lodging=stay is not None), stay


LARGE = (
    "muzeum_miejskie",
    "zoo",
    "bar_mleczny",
    "restauracja_indyjska",
    "park_oliwski",
    "kawiarnia_w_ogrodzie",
    "planszowki",
)


@pytest.mark.parametrize(
    ("who", "cap"),
    [(slice(0, 1), Decimal(330)), (slice(0, 4), Decimal(450))],
    ids=["solo", "group"],
)
def test_larger_brute_force_with_lodging_a_cap_and_two_musts(
    who: slice, cap: Decimal
) -> None:
    data = planning_input(reference(), lodging=True)
    keep = {place_id(k) for k in LARGE}
    must = frozenset({place_id("muzeum_miejskie"), place_id("bar_mleczny")})
    data = data.model_copy(
        update={
            "people": data.people[who],
            "places": tuple(p for p in data.places if p.id in keep),
            "must": must,
        }
    )
    stay = LodgingStay(nights=2, price_per_night=Decimal(100))
    evaluator = PlanEvaluator(data, cost_cap=cap, lodging=stay)
    optimum = best(evaluator, 3, must).objective.value
    result = solve(data, cost_cap=cap, lodging=stay)
    assert must <= set(result.place_ids)
    assert result.cost.total <= cap
    assert result.objective.value <= optimum + 1e-9
    assert result.objective.value >= optimum - 0.005 * abs(optimum)


def local_range(visit_start, visit_end, place, day) -> bool:  # ruff: ignore[missing-type-function-argument]
    hours = place.hours.opening_hours
    if hours is None:
        return True
    if day in hours.closed_dates:
        return False
    weekday = [
        Weekday.MON,
        Weekday.TUE,
        Weekday.WED,
        Weekday.THU,
        Weekday.FRI,
        Weekday.SAT,
        Weekday.SUN,
    ][day.weekday()]
    for rng in hours.weekly.get(weekday, []):
        close = (
            time(23, 59, 59) if rng.close == "24:00" else time.fromisoformat(rng.close)
        )
        if (
            time.fromisoformat(rng.open) <= visit_start.time()
            and visit_end.time() <= close
        ):
            return True
    return False


@pytest.mark.parametrize("key", list(all_scenarios()))
def test_visits_respect_hours_window_and_cost_checked_independently(key: str) -> None:
    scenario = all_scenarios()[key]
    data, stay = instance_for(scenario)
    result = solve(data, lodging=stay)
    by_id = {p.id: p for p in data.places}
    for planned in result.days:
        previous_end = None
        for visit in planned.schedule.visits:
            place = by_id[visit.place_id]
            assert visit.start.time() >= data.trip.day_start
            assert visit.end.time() <= data.trip.day_end
            assert local_range(visit.start, visit.end, place, planned.day)
            assert visit.end - visit.start >= timedelta(minutes=place.typical_visit_min)
            if previous_end is not None:
                assert visit.start >= previous_end
            previous_end = visit.end
    expected = Decimal(0) if stay is None else stay.price_per_night * stay.nights
    for pid in result.place_ids:
        place = by_id[pid]
        for person in data.people:
            rows = [
                r
                for r in place.prices
                if r.unit.value == "person"
                and (
                    r.ticket_category.value == "adult"
                    or (r.ticket_category.value == "child" and person.age <= 17)
                    or (r.ticket_category.value == "senior" and person.age >= 65)
                )
            ]
            if rows:
                expected += min(
                    r.amount if r.verified else r.amount * Decimal("1.15") for r in rows
                )
    assert result.cost.total == expected.quantize(Decimal("0.01"), ROUND_HALF_UP)


def test_solving_a_clone_group_equals_solving_one_person() -> None:
    single = planning_input(solo(), lodging=False)
    alone = solve(single)
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
    group = solve(
        single.model_copy(update={"people": clones, "trip": trip}),
    )
    assert group.plan_hash == alone.plan_hash
