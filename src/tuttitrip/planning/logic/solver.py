"""Deterministic local-search solver of the group goal (docs/algorytm.md, section 9).

Maximises ``J(P) = W(P) - 1000 * V(P)`` (E5) under the hard constraints of E0.
One code path for every ``n >= 1``: a trip of one person is the same equation.

```
start    must places, then the best-key ``add`` while one improves
search   add / remove / replace / move between days / swap a pair of days' places
accept   only a strictly better key  (J, lower cost, lexicographic by place ids)
hard     veto, must, opening hours and the day window and the daily distance
         (``schedule_day``), ``c(P) <= B_max`` (or ``cost_cap``), E0 rejections
soft     floors, own places and tag minima only as a penalty in J: a plan always
         exists and the misses are reported
```

Determinism: people and places are processed in id order, days in date order,
moves in a fixed order, and the work limit is a count of evaluated plans, never
a clock. Every place is visited at most once. Everybody goes everywhere, so
there is no group split (section 9). The result is not proven optimal on large
instances; on the small test instances it matches brute force.

Unknown prices. A plan with any place that could not be fully priced has
``has_unpriced_places``, and its ``c(P) <= B_max`` check is not proof (see
``cost``). That flag is a warning of its own. The E6 consent dialog is driven by
``over_b_do`` (``c(P) > B_do``); the consent itself, with the price per point,
is backend#53.

Work limit. ``max_evaluations`` (default: scaled with candidates x days) counts
evaluated plans. When it ends the search, ``Telemetry.exhausted`` is True and
the result is a local state, not an optimum. The placement of "must" places
ignores the limit.

Hash scope. The plan hash covers the itinerary (days, visits, nights), not the
people or amounts, because section 4 (test 5) gives clones "the same plan (the
same hash)" as one person and section 10 calls the hash the plan's label.

One person's best plan alone (``u*``, E4) is the same ``solve`` on a trip of that
person with ``1/N`` of the budget and the lodging, ``floors={id: 0}`` and no
"must"; ``reference.solo_utility`` builds exactly that input.
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from itertools import combinations
from uuid import UUID
from zoneinfo import ZoneInfo

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.fairness.logic.measure import build_report
from tuttitrip.planning.fairness.logic.objective import (
    GroupObjective,
    PersonOutcome,
    group_objective,
    rank_key,
)
from tuttitrip.planning.fairness.logic.violations import (
    TagRequirement,
    tag_requirements,
)
from tuttitrip.planning.fairness.schemas import FairnessReport
from tuttitrip.planning.logic.cost import PlaceCost, PlanCost, place_cost, plan_cost
from tuttitrip.planning.logic.domains import RequirementOutcome
from tuttitrip.planning.logic.hard_constraints import filter_places
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.plan_hash import plan_hash
from tuttitrip.planning.logic.schedule import (
    DaySchedule,
    DayWindow,
    Infeasible,
    Person,
    schedule_day,
)
from tuttitrip.planning.logic.utility import utility
from tuttitrip.planning.logic.welfare_person import person_scores
from tuttitrip.planning.schemas import (
    DayPlan,
    DomainScores,
    LodgingStay,
    PlanningInput,
    PlanningPerson,
)

SOLVER_NAME = "local-search-v1"
DEFAULT_MAX_EVALUATIONS = 20_000
"""Smallest work limit: evaluated candidate plans (not time)."""
EVALUATIONS_PER_SLOT = 300
"""Work limit per candidate place and day (the larger of the two limits wins)."""
MAX_CACHE_ENTRIES = 200_000
"""A cache is cleared when it grows past this."""
_CENT = Decimal("0.01")

Assignment = tuple[tuple[UUID, ...], ...]
"""Per day (in date order) the chosen place ids, sorted by id."""
_Key = tuple[float, Decimal, tuple[str, ...], tuple[tuple[int, str], ...]]
"""``rank_key`` plus the (day, id) slots, so equal itineraries differ by their days."""
_DayKey = tuple[int, tuple[UUID, ...]]


class SolverConflict(StrEnum):
    """A hard constraint that kept something out of the plan."""

    MUST_REJECTED = "must_rejected"
    """A "must" place fails E0 (veto, hours, stairs, segment) or is unknown."""
    MUST_UNPLACEABLE = "must_unplaceable"
    """A "must" place passes E0 but fits no day within the budget and the day."""
    LODGING_OVER_CAP = "lodging_over_cap"
    """The lodging alone costs more than the cap; the plan has no places."""


@dataclass(frozen=True, slots=True)
class PlannedDay:
    """One day of the result."""

    day: date
    schedule: DaySchedule


@dataclass(frozen=True, slots=True)
class Telemetry:
    """How the plan was computed."""

    solver: str
    steps: int
    """Improving moves applied."""
    evaluations: int
    """Candidate plans evaluated (the work limit counts these)."""
    elapsed_ms: int
    exhausted: bool = False
    """The work limit ended the search: a local state, not an optimum."""


@dataclass(frozen=True, slots=True)
class PlanResult:
    """A computed plan; the service maps it onto ``PlanRead`` (backend#50)."""

    days: tuple[PlannedDay, ...]
    scores: tuple[DomainScores, ...]
    """``q_ij`` and ``u_i`` per person, in id order."""
    objective: GroupObjective
    report: FairnessReport
    cost: PlanCost
    has_unpriced_places: bool
    """A place is not fully priced: warn, and ``c <= B_max`` is not proven."""
    over_b_do: bool
    """``c(P) > B_do``: the organizer's consent (E6, backend#53) is needed."""
    conflicts: tuple[tuple[SolverConflict, UUID | None], ...]
    plan_hash: str
    telemetry: Telemetry

    @property
    def place_ids(self) -> tuple[UUID, ...]:
        """All places of the plan in visiting order."""
        return tuple(v.place_id for d in self.days for v in d.schedule.visits)


@dataclass(frozen=True, slots=True)
class Evaluation:
    """A feasible plan with its objective."""

    assignment: Assignment
    schedules: tuple[DaySchedule, ...]
    scores: tuple[DomainScores, ...]
    objective: GroupObjective
    cost: Decimal
    key: _Key


def _sorted_ids(ids: Sequence[UUID]) -> tuple[UUID, ...]:
    return tuple(sorted(ids, key=str))


class PlanEvaluator:
    """Hard constraints and ``J`` of a plan, with everything precomputed once."""

    def __init__(  # ruff: ignore[too-many-arguments] the whole input of the search
        self,
        data: PlanningInput,
        params: AlgorithmParams = DEFAULT_PARAMS,
        *,
        alpha: float = 1.0,
        cost_cap: Decimal | None = None,
        lodging: LodgingStay | None = None,
        lodging_outcomes: Sequence[RequirementOutcome] | None = None,
        floors: Mapping[UUID, float] | None = None,
    ) -> None:
        """Precompute utilities, prices and the E0 candidates.

        Args:
            data: The planning input.
            params: Algorithm parameters.
            alpha: Fairness slider in 0 to 3.
            cost_cap: Hard cost limit; default ``B_max``.
            lodging: The lodging base, exactly when the trip has nights.
            lodging_outcomes: Requirements checked against it (default none).
            floors: ``f_i^eff`` by person id (E4); default each person's ``f_i``.

        Raises:
            ValueError: When ``lodging`` and ``trip.has_lodging`` disagree.
        """
        trip = data.trip
        if (lodging is not None) != trip.has_lodging:
            msg = "lodging must be given exactly when the trip has nights"
            raise ValueError(msg)
        self.trip = trip
        self.floors = floors or {}
        self.params = params
        self.alpha = alpha
        self.lodging = lodging
        self.outcomes = list(lodging_outcomes or []) if lodging is not None else None
        self.people: tuple[PlanningPerson, ...] = tuple(
            sorted(data.people, key=lambda p: str(p.id))
        )
        self.filtered = filter_places(data, params)
        self.candidates: tuple[PlaceRead, ...] = tuple(
            sorted(self.filtered.accepted, key=lambda p: str(p.id))
        )
        self.places: dict[UUID, PlaceRead] = {p.id: p for p in self.candidates}
        self.requirements: dict[UUID, tuple[TagRequirement, ...]] = {
            person.id: tag_requirements(
                person,
                self.candidates,
                has_lodging=trip.has_lodging,
                params=params,
            )
            for person in self.people
        }
        self.utilities: dict[UUID, dict[UUID, float]] = {
            person.id: {
                p.id: utility(person, p, has_lodging=trip.has_lodging, params=params)
                for p in self.candidates
            }
            for person in self.people
        }
        self.prices: dict[UUID, PlaceCost] = {
            p.id: place_cost(p, self.people, trip.currency, params)
            for p in self.candidates
        }
        self.stay = lodging.price_per_night * lodging.nights if lodging else Decimal(0)
        cap = trip.budget_max if cost_cap is None else cost_cap
        self.cap = max(cap, self.stay)  # lodging alone over the cap: places only lose
        self.lodging_over_cap = self.stay > cap
        zone = ZoneInfo(trip.timezone)
        self.windows = tuple(
            DayWindow(day, zone, trip.day_start, trip.day_end) for day in trip.days
        )
        self.schedulers = tuple(
            Person(p.id, p.daily_km, p.nap_start, p.nap_minutes) for p in self.people
        )
        self._schedule_cache: dict[_DayKey, DaySchedule | None] = {}
        self._cache: dict[Assignment, Evaluation | None] = {}
        self.evaluations = 0

    def _schedule(self, day: int, ids: tuple[UUID, ...]) -> DaySchedule | None:
        key = (day, ids)
        if key not in self._schedule_cache:
            result = schedule_day(
                [self.places[i] for i in ids], self.windows[day], self.schedulers
            )
            if len(self._schedule_cache) >= MAX_CACHE_ENTRIES:
                self._schedule_cache.clear()
            self._schedule_cache[key] = (
                None if isinstance(result, Infeasible) else result
            )
        return self._schedule_cache[key]

    def evaluate(self, assignment: Assignment) -> Evaluation | None:
        """Check the hard constraints and compute ``J``.

        Args:
            assignment: The places of each day.

        Returns:
            The evaluation, or None when a hard constraint fails (a closed
            place, a day that does not fit, the daily distance, the cost cap).
        """
        if assignment in self._cache:
            return self._cache[assignment]
        self.evaluations += 1
        result = self._evaluate(assignment)
        if len(self._cache) >= MAX_CACHE_ENTRIES:
            self._cache.clear()
        self._cache[assignment] = result
        return result

    def _evaluate(self, assignment: Assignment) -> Evaluation | None:
        ids = [i for day in assignment for i in day]
        total = sum((self.prices[i].total for i in ids), Decimal(0)) + self.stay
        total = total.quantize(_CENT, ROUND_HALF_UP)
        if total > self.cap:
            return None
        schedules: list[DaySchedule] = []
        for index, day_ids in enumerate(assignment):
            schedule = self._schedule(index, day_ids)
            if schedule is None:
                return None
            schedules.append(schedule)
        days = [
            DayPlan(
                place_ids=tuple(v.place_id for v in s.visits),
                distance_km=s.distance_km,
                active_min=s.active_min,
            )
            for s in schedules
        ]
        scores = tuple(
            person_scores(
                person,
                days,
                self.places,
                self.utilities[person.id],
                trip=self.trip,
                cost=total,
                lodging=self.outcomes,
                params=self.params,
            )
            for person in self.people
        )
        objective = group_objective(
            [
                PersonOutcome(person, s.welfare, self.floors.get(person.id, person.floor))
                for person, s in zip(self.people, scores, strict=True)
            ],
            days=days,
            places=self.places,
            candidates=self.candidates,
            has_lodging=self.trip.has_lodging,
            requirements=self.requirements,
            alpha=self.alpha,
            params=self.params,
        )
        return Evaluation(
            assignment,
            tuple(schedules),
            scores,
            objective,
            total,
            (
                *rank_key(objective, total, ids),
                tuple(
                    (d, str(i)) for d, day_ids in enumerate(assignment) for i in day_ids
                ),
            ),
        )


def _with(
    current: Assignment, day: int, add: Sequence[UUID] = (), drop: Sequence[UUID] = ()
) -> Assignment:
    return tuple(
        _sorted_ids([*(i for i in ids if i not in drop), *add]) if index == day else ids
        for index, ids in enumerate(current)
    )


def _adds(current: Assignment, unplaced: Sequence[UUID]) -> list[Assignment]:
    return [_with(current, d, add=(p,)) for p in unplaced for d in range(len(current))]


def _neighbours(
    current: Assignment, unplaced: Sequence[UUID], must: frozenset[UUID]
) -> list[Assignment]:
    # Fixed order: add, remove, replace, move between days, swap a pair.
    moves = _adds(current, unplaced)
    for d, ids in enumerate(current):
        for p in ids:
            if p in must:
                continue
            moves.append(_with(current, d, drop=(p,)))
            moves.extend(_with(current, d, add=(b,), drop=(p,)) for b in unplaced)
    for d, ids in enumerate(current):
        for p in ids:
            moves.extend(
                _with(_with(current, d, drop=(p,)), d2, add=(p,))
                for d2 in range(len(current))
                if d2 != d
            )
    for (d1, first), (d2, second) in combinations(enumerate(current), 2):
        for a in first:
            for b in second:
                moved = _with(current, d1, add=(b,), drop=(a,))
                moves.append(_with(moved, d2, add=(a,), drop=(b,)))
    return moves


def _best_improving(
    evaluator: PlanEvaluator,
    best: Evaluation,
    moves: Sequence[Assignment],
    limit: int,
) -> Evaluation | None:
    # The neighbour with the smallest key that beats ``best``; ties keep the first.
    found: Evaluation | None = None
    for move in moves:
        if evaluator.evaluations >= limit:
            break
        candidate = evaluator.evaluate(move)
        if candidate is None or candidate.key >= (found or best).key:
            continue
        found = candidate
    return found


def _place_musts(
    evaluator: PlanEvaluator, start: Evaluation, must: Sequence[UUID]
) -> Evaluation:
    """Put as many "must" places as possible into the plan, trying other days.

    A must placed on its best day may leave no room for a later one, so the days
    are searched depth first (best key first) and the first plan holding every
    must wins; otherwise the plan with the most musts (then the best key). This
    ignores the work limit: it is bounded by days ** musts, and musts are few.

    Args:
        evaluator: The evaluator.
        start: The empty plan.
        must: The "must" places that passed E0, in id order.

    Returns:
        The evaluation holding the musts that could be placed.
    """
    best: tuple[int, Evaluation] = (0, start)

    def place(index: int, current: Evaluation, placed: int) -> bool:
        nonlocal best
        if placed > best[0] or (placed == best[0] and current.key < best[1].key):
            best = (placed, current)
        if index == len(must):
            return placed == len(must)
        options = sorted(
            (
                e
                for d in range(len(current.assignment))
                if (
                    e := evaluator.evaluate(
                        _with(current.assignment, d, add=(must[index],))
                    )
                )
            ),
            key=lambda e: e.key,
        )
        if any(place(index + 1, option, placed + 1) for option in options):
            return True
        place(index + 1, current, placed)  # this must may fit nowhere here
        return False

    place(0, start, 0)
    return best[1]


def solve(  # ruff: ignore[too-many-arguments] the whole input of the search
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
    """Compute the plan that maximises ``J`` under the hard constraints.

    Args:
        data: The planning input (people, trip, candidate places, "must").
        params: Algorithm parameters.
        alpha: Fairness slider in 0 to 3.
        cost_cap: Hard cost limit; default ``B_max``.
        lodging: The lodging base, given exactly when the trip has nights.
        lodging_outcomes: The trip's lodging requirements checked against it.
        floors: ``f_i^eff`` by person id (E4); default each person's ``f_i``.
        max_evaluations: Work limit in evaluated plans; default scales with
            candidates x days (never below ``DEFAULT_MAX_EVALUATIONS``).

    Returns:
        A plan; never "no plan". Misses of the soft guarantees are in
        ``report``, hard conflicts in ``conflicts``.
    """
    started = time.perf_counter()
    evaluator = PlanEvaluator(
        data,
        params,
        alpha=alpha,
        cost_cap=cost_cap,
        lodging=lodging,
        lodging_outcomes=lodging_outcomes,
        floors=floors,
    )
    conflicts: list[tuple[SolverConflict, UUID | None]] = [
        (SolverConflict.MUST_REJECTED, pid) for pid in evaluator.filtered.must_blocked
    ]
    if evaluator.lodging_over_cap:
        conflicts.append((SolverConflict.LODGING_OVER_CAP, None))
    must = _sorted_ids([m for m in data.must if m in evaluator.places])
    limit = max_evaluations or max(
        DEFAULT_MAX_EVALUATIONS,
        EVALUATIONS_PER_SLOT * len(evaluator.candidates) * len(evaluator.windows),
    )
    best, steps, exhausted = _search(evaluator, must, limit)
    placed = {i for ids in best.assignment for i in ids}
    conflicts.extend(
        (SolverConflict.MUST_UNPLACEABLE, pid) for pid in must if pid not in placed
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return _result(evaluator, best, conflicts, steps, elapsed_ms, exhausted=exhausted)


def _search(
    evaluator: PlanEvaluator, must: Sequence[UUID], max_evaluations: int
) -> tuple[Evaluation, int, bool]:
    # Must places first, then adds only, then the whole neighbourhood until stuck.
    empty: Assignment = tuple(() for _ in evaluator.windows)
    start = evaluator.evaluate(empty)
    if start is None:  # pragma: no cover - an empty plan has no hard constraint
        msg = "The empty plan must be feasible"
        raise RuntimeError(msg)
    current = _place_musts(evaluator, start, must)
    must_set = frozenset(must)
    steps = 0
    phase_adds = True
    while True:
        if evaluator.evaluations >= max_evaluations:
            return current, steps, True
        placed = {i for ids in current.assignment for i in ids}
        unplaced = [p.id for p in evaluator.candidates if p.id not in placed]
        moves = (
            _adds(current.assignment, unplaced)
            if phase_adds
            else _neighbours(current.assignment, unplaced, must_set)
        )
        better = _best_improving(evaluator, current, moves, max_evaluations)
        if better is not None:
            current, steps = better, steps + 1
        elif evaluator.evaluations >= max_evaluations:
            return current, steps, True
        elif phase_adds:
            phase_adds = False
        else:
            return current, steps, False


def _result(  # ruff: ignore[too-many-arguments] the pieces of one result
    evaluator: PlanEvaluator,
    best: Evaluation,
    conflicts: Sequence[tuple[SolverConflict, UUID | None]],
    steps: int,
    elapsed_ms: int,
    *,
    exhausted: bool,
) -> PlanResult:
    trip = evaluator.trip
    day_plans = [
        DayPlan(
            place_ids=tuple(v.place_id for v in s.visits),
            distance_km=s.distance_km,
            active_min=s.active_min,
        )
        for s in best.schedules
    ]
    cost = plan_cost(
        day_plans,
        evaluator.places,
        evaluator.people,
        trip=trip,
        lodging=evaluator.lodging,
        params=evaluator.params,
    )
    stay = evaluator.lodging
    digest = plan_hash(
        [
            (day, [(v.place_id, v.start, v.end) for v in s.visits])
            for day, s in zip(trip.days, best.schedules, strict=True)
        ],
        0 if stay is None else stay.nights,
        evaluator.windows[0].timezone,
    )
    return PlanResult(
        days=tuple(map(PlannedDay, trip.days, best.schedules, strict=True)),
        scores=best.scores,
        objective=best.objective,
        report=build_report(best.objective),
        cost=cost,
        has_unpriced_places=bool(cost.unknown_price_place_ids),
        over_b_do=cost.total > trip.budget_to,
        conflicts=tuple(conflicts),
        plan_hash=digest,
        telemetry=Telemetry(
            SOLVER_NAME, steps, evaluator.evaluations, elapsed_ms, exhausted
        ),
    )


__all__ = [
    "DEFAULT_MAX_EVALUATIONS",
    "SOLVER_NAME",
    "Assignment",
    "Evaluation",
    "PlanEvaluator",
    "PlanResult",
    "PlannedDay",
    "SolverConflict",
    "Telemetry",
    "solve",
]
