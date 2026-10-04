"""CP-SAT behind the same interface as the local search (backend#92).

Properties of docs/algorytm.md, section 8, run for both solvers on the fixtures
of backend#38, and the comparison of the two: CP-SAT must reach at least the
objective of the local search, less ``LINEARISATION_TOLERANCE`` (the gap the
tangent approximation of the model may leave; the polishing local search that
follows CP-SAT closes it on every fixture today).
"""

from collections.abc import Generator

import pytest

from tests.domains.test_planning_properties import (
    assignment_of,
    instance,
    small,
)
from tests.fixtures.brute_force import best
from tests.fixtures.city import place_id
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import (
    Scenario,
    over_budget,
    reference,
    solo,
)
from tuttitrip.planning.logic.cpsat import CpSatLimits, CpSatSolver, solve_cpsat
from tuttitrip.planning.logic.hard_constraints import filter_places
from tuttitrip.planning.logic.params import DEFAULT_PARAMS
from tuttitrip.planning.logic.plan_group import plan_group
from tuttitrip.planning.logic.schedule import DAILY_KM_FACTOR
from tuttitrip.planning.logic.solver import (
    SOLVER_NAME as LOCAL_SEARCH_NAME,
)
from tuttitrip.planning.logic.solver import (
    PlanEvaluator,
    Solver,
    SolverConflict,
    solve,
)
from tuttitrip.planning.plans.logic.input_builder import input_hash
from tuttitrip.planning.services.solver_service import configured_solver
from tuttitrip.shared.config.settings import get_settings

LINEARISATION_TOLERANCE = 0.05
"""``J`` points CP-SAT may stay below the local search (errors of the tangents)."""
LIMITS = CpSatLimits(max_deterministic_time=10.0, random_seed=1)
CPSAT = CpSatSolver(LIMITS)
SOLVERS: dict[str, Solver] = {"local_search": solve, "cp_sat": CPSAT}
FIXTURES = pytest.mark.parametrize(
    "scenario", [solo(), reference(), over_budget()], ids=lambda s: s.key
)


@pytest.fixture
def fresh_settings() -> Generator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- both solvers: the same properties ---


@pytest.mark.parametrize("name", list(SOLVERS))
def test_solo_matches_brute_force(name: str) -> None:
    data = small(slice(0, 1))
    optimum = best(PlanEvaluator(data), 2).objective.value
    assert SOLVERS[name](data).objective.value >= optimum - 0.005 * abs(optimum)


@pytest.mark.parametrize("name", list(SOLVERS))
def test_group_matches_brute_force(name: str) -> None:
    data = small(slice(0, 4))
    optimum = best(PlanEvaluator(data), 2).objective.value
    assert SOLVERS[name](data).objective.value >= optimum - 0.005 * abs(optimum)


@pytest.mark.parametrize("name", list(SOLVERS))
def test_veto_is_never_in_the_plan_and_must_is(name: str) -> None:
    data, stay = instance(reference())
    must = place_id("hevelianum")
    result = SOLVERS[name](
        data.model_copy(update={"must": frozenset({must})}), lodging=stay
    )
    assert place_id("restauracja_morska") in data.people[3].vetoes
    assert place_id("restauracja_morska") not in result.place_ids
    assert must in result.place_ids


@FIXTURES
def test_hard_constraints_hold_for_both_solvers(scenario: Scenario) -> None:
    data, stay = instance(scenario)
    accepted = {p.id for p in filter_places(data).accepted}
    for solver in SOLVERS.values():
        result = solver(data, lodging=stay)
        chosen = list(result.place_ids)
        assert len(chosen) == len(set(chosen))
        assert set(chosen) <= accepted
        assert result.cost.total <= data.trip.budget_max
        assert not result.conflicts
        for day in result.days:
            for person in data.people:
                assert day.schedule.distance_km <= DAILY_KM_FACTOR * person.daily_km


# --- CP-SAT against the local search (#38 fixtures) ---


@FIXTURES
def test_cpsat_is_not_worse_than_the_local_search(scenario: Scenario) -> None:
    data, stay = instance(scenario)
    heuristic = solve(data, lodging=stay)
    cpsat = CPSAT(data, lodging=stay)
    assert cpsat.objective.value >= heuristic.objective.value - LINEARISATION_TOLERANCE
    assert cpsat.telemetry.solver == "cp-sat-v1"
    assert cpsat.telemetry.status == "OPTIMAL"
    assert heuristic.telemetry.status is None


def test_cpsat_can_beat_a_local_optimum() -> None:
    # Over budget the local search stops in a local state; CP-SAT finds a plan
    # that misses far less of the soft guarantees.
    data, stay = instance(over_budget())
    assert (
        CPSAT(data, lodging=stay).objective.value
        > solve(data, lodging=stay).objective.value
    )


def test_the_same_data_give_the_same_plan() -> None:
    data, stay = instance(over_budget())
    first = CPSAT(data, lodging=stay)
    second = CPSAT(data, lodging=stay)
    assert first.plan_hash == second.plan_hash
    assert assignment_of(first) == assignment_of(second)
    assert first.objective.value == second.objective.value


@pytest.mark.parametrize("alpha", [0.0, 2.0])
def test_other_alphas_use_the_table_of_phi(alpha: float) -> None:
    data, stay = instance(over_budget())
    heuristic = solve(data, alpha=alpha, lodging=stay)
    cpsat = CPSAT(data, alpha=alpha, lodging=stay)
    assert cpsat.objective.value >= heuristic.objective.value - LINEARISATION_TOLERANCE


def test_a_group_plan_runs_through_either_solver() -> None:
    data, stay = instance(solo())
    for solver in SOLVERS.values():
        group = plan_group(data, lodging=stay, solver=solver)
        assert group.people[0].r == pytest.approx(1)


# --- the limit and the fallback ---


def test_without_time_the_local_search_takes_over_and_the_telemetry_says_so() -> None:
    data, stay = instance(reference())
    result = solve_cpsat(
        data, limits=CpSatLimits(max_deterministic_time=1e-4), lodging=stay
    )
    expected = solve(data, lodging=stay)
    assert result.telemetry.solver == LOCAL_SEARCH_NAME
    assert result.telemetry.status == "UNKNOWN"
    assert result.plan_hash == expected.plan_hash


def test_a_must_that_fits_no_day_is_reported_by_the_fallback() -> None:
    data = planning_input(reference(), lodging=False)
    shut = data.trip.model_copy(update={"days": data.trip.days[:1]})
    musts = frozenset(
        place_id(k) for k in ("hevelianum", "muzeum_miejskie", "zoo", "park_oliwski")
    )
    crowded = data.model_copy(update={"trip": shut, "must": musts})
    result = CPSAT(crowded, lodging=None)
    assert result.telemetry.solver == LOCAL_SEARCH_NAME
    assert result.telemetry.status in {"INFEASIBLE", "UNKNOWN"}
    assert any(c is SolverConflict.MUST_UNPLACEABLE for c, _ in result.conflicts)


# --- the switch ---


@pytest.mark.usefixtures("fresh_settings")
def test_the_local_search_is_the_default() -> None:
    choice = configured_solver()
    assert choice.solver is solve
    assert choice.tag is None


@pytest.mark.usefixtures("fresh_settings")
def test_the_setting_swaps_in_cpsat_with_its_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TUTTITRIP_PLANNING__SOLVER", "cp_sat")
    monkeypatch.setenv("TUTTITRIP_PLANNING__CPSAT_MAX_DETERMINISTIC_TIME", "3.5")
    monkeypatch.setenv("TUTTITRIP_PLANNING__CPSAT_RANDOM_SEED", "7")
    get_settings.cache_clear()
    choice = configured_solver()
    assert choice.solver == CpSatSolver(CpSatLimits(3.5, 7))
    assert choice.tag == "cp_sat:7"


def test_the_solver_is_part_of_the_input_hash() -> None:
    data, _ = instance(solo())
    plain = input_hash(data, 1.0, "default", DEFAULT_PARAMS)
    assert input_hash(data, 1.0, "default", DEFAULT_PARAMS, None) == plain
    assert input_hash(data, 1.0, "default", DEFAULT_PARAMS, "cp_sat:1") != plain
