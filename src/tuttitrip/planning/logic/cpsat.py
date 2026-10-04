"""CP-SAT solver of the group goal, behind the same interface as the local search.

docs/algorytm.md, section 9 names CP-SAT with a tangent linearisation of the
logarithm as the production replacement of the local search. The goal and the
hard constraints are the same (``J = W - 1000 V`` of E5 under E0); only the
search for the places changes.

```
x[p,d]   place p on day d, at most once; "must" exactly once
E0       c(P) <= cap (integer cents), the day's visit minutes fit the window,
         the daily distance <= 1.5 * min D_i, a place only on days it can open
E2       attractions and food: per day y_id <= tangents of 100 (1 - exp(-k s_id));
         pace: the two excesses of each day are linear (exact, L_d and A_d are sums);
         cost: concave piecewise linear in c; lodging: a constant
E3       T_ij <= tangents of ln(1 + q_ij);  L_i = sum_j a_ij T_ij = ln(1 + u_i)
E5       W = sum_i w_i phi_alpha(u_i) as a table over L_i (any alpha), the floor
         term as a table over L_i, own places and tag minima as penalty variables
```

Every nonlinear term is increasing (or decreasing) in the direction the goal pushes,
so a tangent upper bound is tight at the optimum; its error is the only gap to the
exact ``J`` (tangents every few points, below 0.5 point of ``q``). What the model
leaves out is checked by the exact evaluator of the local search: opening hours and
the order inside a day. A day that ``schedule_day`` rejects becomes a no-good cut and
the model is solved again. The chosen plan then goes through the local search once,
started from it, which repairs the linearisation error and the rounding of cents.

Determinism: one worker, a fixed seed, variables created in id order and a limit in
*deterministic time* (``max_deterministic_time``), never a clock. The local search is
the fallback when CP-SAT finds no plan within the limit (``UNKNOWN``), or the model is
infeasible (for example a "must" that fits no day); the telemetry records which solver
produced the plan and the CP-SAT status.

The only module outside ``solver`` that imports ``ortools``. Pure otherwise.
"""

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from uuid import UUID

from ortools.sat.python import cp_model

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.fairness.logic.violations import (
    OWN_PLACE_WEIGHT,
    carries_tag,
    floor_term,
)
from tuttitrip.planning.logic.domains import RequirementOutcome, lodging_score
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.schedule import DAILY_KM_FACTOR
from tuttitrip.planning.logic.solver import (
    Assignment,
    PlanEvaluator,
    PlanResult,
    SolverConflict,
    solve,
)
from tuttitrip.planning.logic.utility import match, place_domain
from tuttitrip.planning.logic.welfare_person import renormalised_pool
from tuttitrip.planning.schemas import LodgingStay, PlanningInput, PlanningPerson
from tuttitrip.profiles.preferences.schemas import ImportanceDomain

SOLVER_NAME: Final = "cp-sat-v1"
"""Name in the telemetry; the local search is ``local-search-v1``."""
FALLBACK_STATUS: Final = "FALLBACK"
"""Status in the telemetry when the local search produced the plan instead."""

Q_SCALE: Final = 100
"""A satisfaction ``q`` in hundredths of a point (0 to 10000)."""
Q_MAX: Final = 100 * Q_SCALE
LN_SCALE: Final = 1000
"""``ln(1 + q)`` in thousandths."""

SHARE_SCALE: Final = 1000
"""A pool share ``a_ij`` in thousandths."""
RAW_PER_LN: Final = LN_SCALE * SHARE_SCALE
"""Units of ``raw = sum a_ij T_ij`` per unit of ``ln(1 + u)``."""
INDEX_STEP: Final = RAW_PER_LN // 50
"""Resolution of the tables over ``ln(1 + u)``: 0.02, in units of ``raw``."""
LOAD_SCALE: Final = 1000
"""The saturation load of a day in thousandths."""
WEIGHT_SCALE: Final = 100
"""A person weight ``w_i`` in hundredths."""
PHI_SCALE: Final = 1_000_000
"""``phi_alpha(u)`` in millionths."""
TERM_SCALE: Final = 10_000
"""A fraction of ``V`` (floor shortfall) in ten-thousandths."""
J_SCALE: Final = PHI_SCALE * WEIGHT_SCALE
"""One unit of ``J`` in the objective."""
KM_SCALE: Final = 1000
"""Daily distance in metres."""
PACE_SCALE: Final = 10_000
"""An excess of the pace term in ten-thousandths."""
PACE_HEADROOM: Final = 100 * PACE_SCALE
"""Largest excess of one day the model can hold (far above anything real)."""
LINE_SCALE: Final = 1_000_000
"""Denominator of a linearised slope, so that integer coefficients keep precision."""
CENTS: Final = 100
"""Cents per currency unit."""
MIN_PER_HOUR: Final = 60
"""Minutes in an hour, for the day window."""

ATTRACTION_DAY_SATURATION: Final = (
    0.0,
    8.0,
    16.0,
    24.0,
    32.0,
    40.0,
    48.0,
    56.0,
    64.0,
    72.0,
    80.0,
    86.0,
    91.0,
    95.0,
    98.0,
    99.5,
)
"""Levels of ``y = 100 (1 - exp(-k s))`` where tangents are drawn (steps in ``y``)."""
LN_TANGENTS: Final = (0, 1, 2, 3, 5, 8, 12, 17, 23, 30, 38, 47, 57, 68, 80, 100)
"""Points ``q`` where tangents of ``ln(1 + q)`` are drawn."""
MAX_CUT_ROUNDS: Final = 25
"""Rounds of solve, check and cut before the local search takes over."""
MIN_TIME_LEFT: Final = 1e-3
"""Deterministic time below which another round is not started."""
CHEAP_TIE: Final = 1
"""Objective weight of one cent: only breaks ties between equal plans."""

_CENT: Final = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class CpSatLimits:
    """How long and with which seed CP-SAT may search (from the settings)."""

    max_deterministic_time: float = 10.0
    """Deterministic seconds of the whole solve, all rounds together."""
    random_seed: int = 1


@dataclass(frozen=True, slots=True)
class _Line:
    """``y <= slope * x + intercept``."""

    slope: float
    intercept: float


def _tangent(
    f: Callable[[float], float], df: Callable[[float], float], at: float
) -> _Line:
    slope = df(at)
    return _Line(slope, f(at) - slope * at)


def _bound(
    model: cp_model.CpModel, y: cp_model.IntVar, x: cp_model.LinearExpr, line: _Line
) -> None:
    # y <= slope * x + intercept, with integer coefficients.
    model.add(
        LINE_SCALE * y
        <= round(LINE_SCALE * line.slope) * x + round(LINE_SCALE * line.intercept)
    )


def _cents(amount: Decimal) -> int:
    return int((amount * CENTS).quantize(Decimal(1), ROUND_HALF_UP))


def _ln_table(size: int, f: Callable[[float], float]) -> list[int]:
    # f at the middle of each step of ln(1 + u), with u capped at 100.
    return [
        round(f(min(100.0, math.expm1((i + 0.5) * INDEX_STEP / RAW_PER_LN))))
        for i in range(size + 1)
    ]


def _phi(alpha: float) -> Callable[[float], float]:
    if math.isclose(alpha, 1.0):
        return lambda u: PHI_SCALE * math.log1p(u)
    return lambda u: PHI_SCALE * math.pow(1 + u, 1 - alpha) / (1 - alpha)


class _Build:
    """The CP-SAT model of one planning input."""

    def __init__(
        self, evaluator: PlanEvaluator, data: PlanningInput, alpha: float
    ) -> None:
        self.ev = evaluator
        self.data = data
        self.alpha = alpha
        self.params = evaluator.params
        self.model = cp_model.CpModel()
        self.days = len(evaluator.windows)
        self.x: dict[tuple[UUID, int], cp_model.IntVar] = {}
        self.terms: list[cp_model.LinearExpr | int] = []
        self.ln_size = math.ceil(math.log(101) * RAW_PER_LN / INDEX_STEP)
        self._places()
        self._day_limits()
        self._cost()
        for person in evaluator.people:
            self._person(person)
        cheapness = -CHEAP_TIE * self.cost_cents
        self.model.maximize(sum(self.terms) + cheapness)

    # --- places and the hard constraints --------------------------------------

    def _places(self) -> None:
        ev = self.ev
        for place in ev.candidates:
            for day in range(self.days):
                if ev.day_feasible(day, (place.id,)):
                    self.x[place.id, day] = self.model.new_bool_var(
                        f"x_{place.id}_{day}"
                    )
        self.chosen: dict[UUID, cp_model.LinearExpr] = {}
        for place in ev.candidates:
            slots = [
                self.x[place.id, d] for d in range(self.days) if (place.id, d) in self.x
            ]
            used = sum(slots)
            self.chosen[place.id] = used  # ty: ignore[invalid-assignment]
            if place.id in self.data.must:
                self.model.add(used == 1)  # empty: infeasible, caught by the caller
            else:
                self.model.add(used <= 1)

    def _day_limits(self) -> None:
        ev = self.ev
        window = ev.trip
        minutes = (
            (window.day_end.hour - window.day_start.hour) * MIN_PER_HOUR
            + window.day_end.minute
            - window.day_start.minute
        )
        km = DAILY_KM_FACTOR * min(p.daily_km for p in ev.people)
        for day in range(self.days):
            here = [
                (p, self.x[p.id, day]) for p in ev.candidates if (p.id, day) in self.x
            ]
            self.model.add(sum(p.typical_visit_min * v for p, v in here) <= minutes)
            self.model.add(
                sum(round(p.segment_km * KM_SCALE) * v for p, v in here)
                <= math.floor(km * KM_SCALE + 1e-6)
            )

    def _cost(self) -> None:
        ev = self.ev
        stay = _cents(ev.stay)
        self.cost_cents = stay + sum(
            _cents(ev.prices[p.id].total) * self.chosen[p.id] for p in ev.candidates
        )
        self.model.add(self.cost_cents <= _cents(ev.cap))
        trip = ev.trip
        self.q_cost = self.model.new_int_var(0, Q_MAX, "q_cost")
        low, high, top = (
            _cents(b) for b in (trip.budget_from, trip.budget_to, trip.budget_max)
        )
        comfort = round(ev.params.cost_comfort * Q_SCALE)
        # 100 up to B_od, linear to the comfort at B_do, linear to 0 at B_max: one
        # zone is on, and in it q is bounded by that zone's line (the function is
        # not concave for every flex, so a plain minimum of lines would be wrong).
        zones = [self.model.new_bool_var(f"zone_{k}") for k in range(3)]
        self.model.add_exactly_one(zones)
        cost = self.cost_cents
        self.model.add(cost <= low).only_enforce_if(zones[0])
        self.model.add(cost >= low).only_enforce_if(zones[1])
        self.model.add(cost <= high).only_enforce_if(zones[1])
        self.model.add(cost >= high).only_enforce_if(zones[2])
        if high > low:
            self.model.add(
                self.q_cost * (high - low)
                <= Q_MAX * (high - low) - (Q_MAX - comfort) * (cost - low)
            ).only_enforce_if(zones[1])
        if top > high:
            self.model.add(
                self.q_cost * (top - high)
                <= comfort * (top - high) - comfort * (cost - high)
            ).only_enforce_if(zones[2])
        else:
            self.model.add(self.q_cost <= comfort).only_enforce_if(zones[2])

    # --- one person ---------------------------------------------------------------

    def _person(self, person: PlanningPerson) -> None:
        ev = self.ev
        shares = renormalised_pool(person, has_lodging=ev.trip.has_lodging)
        ln_terms: list[cp_model.LinearExpr | int] = []
        for domain, share in shares.items():
            if share <= 0:
                continue
            q = self._satisfaction(person, domain)
            ln = self._ln(q, f"t_{person.id}_{domain.value}")
            ln_terms.append(round(share * SHARE_SCALE) * ln)
        raw = sum(ln_terms)
        index = self.model.new_int_var(0, self.ln_size, f"l_{person.id}")
        self.model.add(INDEX_STEP * index <= raw)
        weight = round(person.weight * WEIGHT_SCALE)
        if math.isclose(self.alpha, 1.0):  # phi_1 = ln(1 + u): linear in raw
            self.terms.append(weight * raw)
        else:
            table = _ln_table(self.ln_size, _phi(self.alpha))
            phi = self.model.new_int_var(min(table), max(table), f"phi_{person.id}")
            self.model.add_element(index, table, phi)
            self.terms.append(weight * phi)
        self._violations(person, index)

    def _ln(self, q: cp_model.LinearExpr | int, name: str) -> cp_model.LinearExpr | int:
        # T <= ln(1 + q) in thousandths, as the lowest of tangents.
        if isinstance(q, int):
            return round(LN_SCALE * math.log1p(q / Q_SCALE))
        t = self.model.new_int_var(0, round(LN_SCALE * math.log(101)) + 1, name)
        for knot in LN_TANGENTS:
            # in units of q_scaled: d/dQ of LN_SCALE * ln(1 + Q / Q_SCALE)
            _bound(
                self.model,
                t,
                q,
                _tangent(
                    lambda v: LN_SCALE * math.log1p(v / Q_SCALE),
                    lambda v: LN_SCALE / (Q_SCALE + v),
                    knot * Q_SCALE,
                ),
            )
        return t

    def _satisfaction(
        self, person: PlanningPerson, domain: ImportanceDomain
    ) -> cp_model.LinearExpr | int:
        if domain is ImportanceDomain.COST:
            return self.q_cost
        if domain is ImportanceDomain.LODGING:
            outcomes = self.ev.outcomes or []
            return round(lodging_score(outcomes, self.params) * Q_SCALE)
        if domain is ImportanceDomain.PACE:
            return self._pace(person)
        return self._saturating(person, domain)

    def _saturating(
        self, person: PlanningPerson, domain: ImportanceDomain
    ) -> cp_model.LinearExpr:
        # (1/D) sum_d 100 (1 - exp(-k * load_d)); each day concave, so tangents.
        ev = self.ev
        params = self.params
        food = domain is ImportanceDomain.FOOD
        kappa = params.kappa_food if food else params.kappa_attractions
        days_sum: list[cp_model.IntVar] = []
        for day in range(self.days):
            load = sum(
                round(LOAD_SCALE * self._load(person, p, food=food)) * self.x[p.id, day]
                for p in ev.candidates
                if (p.id, day) in self.x and (place_domain(p) is domain)
            )
            y = self.model.new_int_var(0, Q_MAX, f"y_{person.id}_{domain.value}_{day}")
            if isinstance(load, int):
                self.model.add(y == 0)
            else:
                for level in ATTRACTION_DAY_SATURATION:
                    at = -math.log(1 - level / 100) / kappa * LOAD_SCALE
                    _bound(
                        self.model,
                        y,
                        load,
                        _tangent(
                            lambda s: Q_MAX * (1 - math.exp(-kappa * s / LOAD_SCALE)),
                            lambda s: (
                                Q_MAX
                                * kappa
                                / LOAD_SCALE
                                * math.exp(-kappa * s / LOAD_SCALE)
                            ),
                            at,
                        ),
                    )
            days_sum.append(y)
        q = self.model.new_int_var(0, Q_MAX, f"q_{person.id}_{domain.value}")
        self.model.add(self.days * q <= sum(days_sum))
        return q

    def _load(self, person: PlanningPerson, place: PlaceRead, *, food: bool) -> float:
        u = self.ev.utilities[person.id][place.id] / 100
        return u if food else place.typical_visit_min / self.params.tau_ref_min * u

    def _pace(self, person: PlanningPerson) -> cp_model.LinearExpr:
        # 100 (1 - min(1, mean_d [(L_d - D_i)+ / 2 D_i + (A_d - A_i)+ / 2 A_i])).
        ev = self.ev
        excess: list[cp_model.IntVar] = []
        limit_m = round(person.daily_km * KM_SCALE)
        for day in range(self.days):
            here = [
                (p, self.x[p.id, day]) for p in ev.candidates if (p.id, day) in self.x
            ]
            dist = sum(round(p.segment_km * KM_SCALE) * v for p, v in here)
            active = sum((p.typical_visit_min + p.transfer_min) * v for p, v in here)
            over_km = self.model.new_int_var(0, PACE_HEADROOM, f"ok_{person.id}_{day}")
            over_min = self.model.new_int_var(0, PACE_HEADROOM, f"om_{person.id}_{day}")
            self.model.add(over_km * (2 * limit_m) >= PACE_SCALE * (dist - limit_m))
            self.model.add(
                over_min * (2 * person.active_min)
                >= PACE_SCALE * (active - person.active_min)
            )
            excess += [over_km, over_min]
        capped = self.model.new_int_var(0, self.days * PACE_SCALE, f"cap_{person.id}")
        self.model.add_min_equality(capped, [sum(excess), self.days * PACE_SCALE])
        q = self.model.new_int_var(0, Q_MAX, f"q_{person.id}_pace")
        self.model.add(self.days * q <= self.days * Q_MAX - capped)
        return q

    def _violations(self, person: PlanningPerson, index: cp_model.IntVar) -> None:
        ev = self.ev
        penalty = self.params.violation_penalty
        alone = len(ev.people) == 1
        floor = 0.0 if alone else ev.floors.get(person.id, person.floor)
        if floor > 0:
            table = _ln_table(self.ln_size, lambda u: TERM_SCALE * floor_term(u, floor))
            term = self.model.new_int_var(0, TERM_SCALE, f"f_{person.id}")
            self.model.add_element(index, table, term)
            self.terms.append(-round(penalty * J_SCALE / TERM_SCALE) * term)
        own = [
            p
            for p in ev.candidates
            if match(person, p, self.params) >= self.params.own_place_match
        ]
        for day in range(self.days):
            slots = [self.x[p.id, day] for p in own if (p.id, day) in self.x]
            if slots:
                has = self.model.new_bool_var(f"own_{person.id}_{day}")
                self.model.add(has <= sum(slots))
                self.terms.append(round(OWN_PLACE_WEIGHT * penalty * J_SCALE) * has)
        for need in ev.requirements[person.id]:
            have = sum(
                self.chosen[p.id] for p in ev.candidates if carries_tag(p, need.tag)
            )
            short = self.model.new_int_var(0, need.required, f"tag_{person.id}")
            self.model.add(short >= need.required - have)
            self.terms.append(-round(penalty * J_SCALE / need.required) * short)

    def forbid(self, day: int, place_ids: Sequence[UUID]) -> None:
        """Cut: these places may not all be on this day.

        Args:
            day: Index of the day.
            place_ids: Places that ``schedule_day`` could not put in one day.
        """
        slots = [self.x[pid, day] for pid in place_ids]
        self.model.add(sum(slots) <= len(slots) - 1)

    def cut(self, candidate: Assignment, evaluator: PlanEvaluator) -> None:
        """Forbid what the exact evaluator rejected in a candidate plan.

        Args:
            candidate: The assignment CP-SAT chose.
            evaluator: The exact evaluator of the local search.
        """
        rejected = False
        for day, ids in enumerate(candidate):
            if ids and not evaluator.day_feasible(day, ids):
                self.forbid(day, ids)
                rejected = True
        if not rejected:  # every day is fine: the cost cap (rounding of cents)
            self.model.add(
                sum(self.x[pid, d] for d, ids in enumerate(candidate) for pid in ids)
                <= sum(len(ids) for ids in candidate) - 1
            )

    def assignment(self, solver: cp_model.CpSolver) -> Assignment:
        """Read the chosen places of every day.

        Args:
            solver: A solver that found a solution.

        Returns:
            The places of each day, sorted by id.
        """
        return tuple(
            tuple(
                sorted(
                    (
                        pid
                        for (pid, d), v in self.x.items()
                        if d == day and solver.boolean_value(v)
                    ),
                    key=str,
                )
            )
            for day in range(self.days)
        )


def solve_cpsat(  # ruff: ignore[too-many-arguments] the whole input of the search
    data: PlanningInput,
    params: AlgorithmParams = DEFAULT_PARAMS,
    *,
    limits: CpSatLimits | None = None,
    alpha: float = 1.0,
    cost_cap: Decimal | None = None,
    lodging: LodgingStay | None = None,
    lodging_outcomes: Sequence[RequirementOutcome] | None = None,
    floors: Mapping[UUID, float] | None = None,
    max_evaluations: int | None = None,
) -> PlanResult:
    """Compute the plan that maximises ``J`` with CP-SAT (see the module docstring).

    Args:
        data: The planning input (people, trip, candidate places, "must").
        params: Algorithm parameters.
        limits: Deterministic time and seed of CP-SAT; default ``CpSatLimits()``.
        alpha: Fairness slider in 0 to 3.
        cost_cap: Hard cost limit; default ``B_max``.
        lodging: The lodging base, given exactly when the trip has nights.
        lodging_outcomes: The trip's lodging requirements checked against it.
        floors: ``f_i^eff`` by person id (E4); default each person's ``f_i``.
        max_evaluations: Work limit of the local search that polishes the result
            (and of the fallback run).

    Returns:
        A plan; never "no plan". ``telemetry.solver`` tells who produced it and
        ``telemetry.status`` the CP-SAT status.
    """
    started = time.perf_counter()
    limits = limits or CpSatLimits()
    evaluator = PlanEvaluator(
        data,
        params,
        alpha=alpha,
        cost_cap=cost_cap,
        lodging=lodging,
        lodging_outcomes=lodging_outcomes,
        floors=floors,
    )
    status = "UNKNOWN"
    chosen: Assignment | None = None
    if (
        not evaluator.lodging_over_cap
    ):  # else nothing to choose: the fallback reports it
        build = _Build(evaluator, data, alpha)
        left = limits.max_deterministic_time
        for _ in range(MAX_CUT_ROUNDS):
            if left <= MIN_TIME_LEFT:
                break
            solver = cp_model.CpSolver()
            solver.parameters.num_workers = 1
            solver.parameters.random_seed = limits.random_seed
            solver.parameters.max_deterministic_time = left
            outcome = solver.solve(build.model)
            left -= solver.response_proto.deterministic_time
            status = str(solver.status_name(outcome))
            if outcome not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
                break
            candidate = build.assignment(solver)
            if evaluator.evaluate(candidate) is not None:
                chosen = candidate
                break
            build.cut(candidate, evaluator)
    result = solve(
        data,
        params,
        alpha=alpha,
        cost_cap=cost_cap,
        lodging=lodging,
        lodging_outcomes=lodging_outcomes,
        floors=floors,
        max_evaluations=max_evaluations,
        start=chosen,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    telemetry = result.telemetry
    if chosen is None:  # the local search made the plan
        return replace(
            result, telemetry=replace(telemetry, status=status, elapsed_ms=elapsed_ms)
        )
    return replace(
        result,
        telemetry=replace(
            telemetry,
            solver=SOLVER_NAME,
            status=status,
            elapsed_ms=elapsed_ms,
            exhausted=telemetry.exhausted or status != "OPTIMAL",
        ),
    )


@dataclass(frozen=True, slots=True)
class CpSatSolver:
    """CP-SAT as a ``Solver``: the same call as ``solve`` with its limits bound."""

    limits: CpSatLimits = CpSatLimits()

    def __call__(  # ruff: ignore[too-many-arguments] the whole input of the search
        self,
        data: PlanningInput,
        params: AlgorithmParams = DEFAULT_PARAMS,
        *,
        alpha: float = 1.0,
        cost_cap: Decimal | None = None,
        lodging: LodgingStay | None = None,
        lodging_outcomes: Sequence[RequirementOutcome] | None = None,
        floors: Mapping[UUID, float] | None = None,
        max_evaluations: int | None = None,
    ) -> PlanResult:
        """Compute the plan that maximises ``J`` (see ``solve_cpsat``).

        Args:
            data: The planning input.
            params: Algorithm parameters.
            alpha: Fairness slider in 0 to 3.
            cost_cap: Hard cost limit; default ``B_max``.
            lodging: The lodging base, given exactly when the trip has nights.
            lodging_outcomes: The trip's lodging requirements checked against it.
            floors: ``f_i^eff`` by person id (E4).
            max_evaluations: Work limit of the polishing local search.

        Returns:
            A plan; never "no plan".
        """
        return solve_cpsat(
            data,
            params,
            limits=self.limits,
            alpha=alpha,
            cost_cap=cost_cap,
            lodging=lodging,
            lodging_outcomes=lodging_outcomes,
            floors=floors,
            max_evaluations=max_evaluations,
        )


__all__ = ["SOLVER_NAME", "CpSatLimits", "CpSatSolver", "SolverConflict", "solve_cpsat"]
