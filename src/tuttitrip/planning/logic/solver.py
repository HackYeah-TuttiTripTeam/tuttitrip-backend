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

import math
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from itertools import combinations
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.fairness.logic.measure import build_report
from tuttitrip.planning.fairness.logic.objective import (
    ROUNDING,
    GroupObjective,
    PersonOutcome,
    group_objective,
)
from tuttitrip.planning.fairness.logic.violations import (
    TagRequirement,
    tag_requirements,
)
from tuttitrip.planning.fairness.schemas import FairnessReport
from tuttitrip.planning.logic.cost import PlaceCost, PlanCost, place_cost, plan_cost
from tuttitrip.planning.logic.domains import RequirementOutcome, lodging_score
from tuttitrip.planning.logic.hard_constraints import filter_places
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.plan_hash import plan_hash
from tuttitrip.planning.logic.schedule import (
    DaySchedule,
    DayWindow,
    Infeasible,
    Person,
    ScheduledVisit,
    schedule_day,
)
from tuttitrip.planning.logic.utility import match, utility
from tuttitrip.planning.logic.welfare_person import person_scores
from tuttitrip.planning.schemas import (
    DayPlan,
    DomainScores,
    LodgingOption,
    LodgingOutcome,
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
_Key = tuple[float, Decimal, tuple[str, ...], tuple[tuple[int, str], ...], int]
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
    status: str | None = None
    """Solver status when it has one (CP-SAT: ``OPTIMAL``, ``FEASIBLE``, ...)."""


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
    lodging: tuple[LodgingOption, ...] = ()
    """The base of each night, in night order (empty without nights)."""
    lodging_delta: LodgingDelta | None = None
    """What the exceptional nights add over the plain base (backend#71)."""

    @property
    def place_ids(self) -> tuple[UUID, ...]:
        """All places of the plan in visiting order."""
        return tuple(v.place_id for v in self.all_visits)

    @property
    def all_visits(self) -> tuple[ScheduledVisit, ...]:
        """All visits of the plan, day after day."""
        return tuple(v for d in self.days for v in d.schedule.visits)


@dataclass(frozen=True, slots=True)
class LodgingDelta:
    """Exceptional nights: the extra cost and the points they give each person."""

    nights: tuple[int, ...]
    """1-based nights that use another base."""
    extra_cost: Decimal
    extra_points: tuple[tuple[UUID, float], ...]
    """``u_i`` with the exceptional nights minus without, per person (id order)."""


@dataclass(frozen=True, slots=True)
class Evaluation:
    """A feasible plan with its objective."""

    assignment: Assignment
    schedules: tuple[DaySchedule, ...]
    scores: tuple[DomainScores, ...]
    objective: GroupObjective
    cost: Decimal
    key: _Key
    lodging_index: int = 0


@dataclass(frozen=True, slots=True)
class LodgingPlan:
    """The base of each night; its cost and its mean satisfaction (E2, E6)."""

    nights: tuple[LodgingOption, ...]

    @property
    def total(self) -> Decimal:
        """``sum_nights price`` (E6)."""
        return sum((o.price_per_night for o in self.nights), Decimal(0))

    @property
    def q(self) -> float:
        """Mean over the nights of ``100 * S_h`` of that night's base (E2)."""
        return math.fsum(_option_score(o) for o in self.nights) / len(self.nights)


def _option_score(option: LodgingOption) -> float:
    return lodging_score(
        [RequirementOutcome(o.hard, o.status) for o in option.outcomes]
    )


def lodging_plans(
    options: Sequence[LodgingOption], nights: int, max_exceptional: int
) -> list[LodgingPlan]:
    """Every lodging plan the search may choose from.

    One base for all nights (section 9) for each option, and, with an
    allowance of exceptional nights (backend#71, an extension), a base with the
    last ``k`` nights spent in another option, ``1 <= k <= max_exceptional``.

    Args:
        options: The lodging options, in id order.
        nights: Number of nights.
        max_exceptional: ``N_max``; 0 gives the plain section 9 plans.

    Returns:
        The plans in a fixed order (all-one-base first), without the ones another
        plan beats on both cost and satisfaction.
    """
    plans = [LodgingPlan((o,) * nights) for o in options]
    plans.extend(
        LodgingPlan((base,) * (nights - k) + (other,) * k)
        for base in options
        for other in options
        if other.place_id != base.place_id
        for k in range(1, min(max_exceptional, nights - 1) + 1)
    )
    return _undominated(plans)


def _undominated(plans: list[LodgingPlan]) -> list[LodgingPlan]:
    # A plan that costs at least as much and satisfies no more than another one
    # can never give a better J (E2 lodging and E6 cost both move the right way
    # for the other plan, and a lower cost only loosens the cap), so it is not
    # searched. Equal plans keep the first.
    kept: list[LodgingPlan] = []
    for plan in plans:
        if any(other.q >= plan.q and other.total <= plan.total for other in kept):
            continue
        kept = [
            other
            for other in kept
            if not (plan.q >= other.q and plan.total <= other.total)
        ]
        kept.append(plan)
    return kept


def sorted_ids(ids: Iterable[UUID]) -> tuple[UUID, ...]:
    """Place ids in the canonical (text) order.

    Args:
        ids: The ids.

    Returns:
        The ids as a sorted tuple.
    """
    return tuple(sorted(ids, key=str))


def _lodging_plans_of(
    data: PlanningInput,
    params: AlgorithmParams,
    lodging: LodgingStay | None,
    outcomes: Sequence[RequirementOutcome] | None,
) -> list[LodgingPlan]:
    # A given stay is the one base; otherwise the plans come from the options.
    trip = data.trip
    if lodging is not None:
        option = LodgingOption(
            place_id=lodging.place_id or UUID(int=0),
            name="",
            lat=0.0,
            lon=0.0,
            price_per_night=lodging.price_per_night,
            outcomes=tuple(
                LodgingOutcome(feature="", hard=o.hard, status=o.status)
                for o in outcomes or ()
            ),
        )
        return [LodgingPlan((option,) * lodging.nights)]
    nights = len(trip.days) - 1
    if not trip.has_lodging or nights < 1 or not data.lodgings:
        return []
    options = sorted(data.lodgings, key=lambda o: str(o.place_id))
    return lodging_plans(options, nights, params.max_exceptional_nights)


class PlanEvaluator:
    """Hard constraints and ``J`` of a plan, with everything precomputed once."""

    def choose_lodging(self, index: int) -> None:
        """Evaluate the following plans with the given lodging plan.

        Args:
            index: Index into ``lodging_plans``.
        """
        self.lodging_index = index

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
        utility_factors: Mapping[UUID, float] | None = None,
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
            utility_factors: Factor on ``u_ip`` by place id (the rain extension,
                backend#74); default 1.

        Raises:
            ValueError: When ``lodging`` and ``trip.has_lodging`` disagree.
        """
        trip = data.trip
        plans = _lodging_plans_of(data, params, lodging, lodging_outcomes)
        if bool(plans) != trip.has_lodging:
            msg = "lodging must be given exactly when the trip has nights"
            raise ValueError(msg)
        self.trip = trip
        self.floors = floors or {}
        self.params = params
        self.alpha = alpha
        self.lodging_plans = plans
        self.lodging_index = 0
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
        self.matches: dict[UUID, dict[UUID, float]] = {
            person.id: {p.id: match(person, p, params) for p in self.candidates}
            for person in self.people
        }
        self.sid = {p.id: str(p.id) for p in self.candidates}
        self.utilities: dict[UUID, dict[UUID, float]] = {
            person.id: {
                p.id: utility(person, p, has_lodging=trip.has_lodging, params=params)
                * (utility_factors or {}).get(p.id, 1.0)
                for p in self.candidates
            }
            for person in self.people
        }
        self.prices: dict[UUID, PlaceCost] = {
            p.id: place_cost(p, self.people, trip.currency, params)
            for p in self.candidates
        }
        self.stays = [p.total for p in plans] or [Decimal(0)]
        self.lodging_q = [p.q for p in plans] or [None]
        cap = trip.budget_max if cost_cap is None else cost_cap
        # Lodging alone over the cap: places only lose (the plan still exists).
        self.caps = [max(cap, stay) for stay in self.stays]
        self.over_cap = [stay > cap for stay in self.stays]
        zone = ZoneInfo(trip.timezone)
        self.windows = tuple(
            DayWindow(day, zone, trip.day_start, trip.day_end) for day in trip.days
        )
        self.schedulers = tuple(
            Person(p.id, p.daily_km, p.nap_start, p.nap_minutes) for p in self.people
        )
        self._schedule_cache: dict[_DayKey, DaySchedule | None] = {}
        self._cache: dict[tuple[int, Assignment], Evaluation | None] = {}
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

    def day_feasible(self, day: int, place_ids: Sequence[UUID]) -> bool:
        """Whether the places can make one day (hours, window, daily distance).

        Args:
            day: Index of the day in date order.
            place_ids: The candidate places of the day.

        Returns:
            False when ``schedule_day`` rejects them.
        """
        return self._schedule(day, sorted_ids(place_ids)) is not None

    def evaluate(self, assignment: Assignment) -> Evaluation | None:
        """Check the hard constraints and compute ``J``.

        Uses the lodging plan chosen with ``choose_lodging``.

        Args:
            assignment: The places of each day.

        Returns:
            The evaluation, or None when a hard constraint fails (a closed
            place, a day that does not fit, the daily distance, the cost cap).
        """
        key = (self.lodging_index, assignment)
        if key in self._cache:
            return self._cache[key]
        self.evaluations += 1
        result = self._evaluate(assignment)
        if len(self._cache) >= MAX_CACHE_ENTRIES:
            self._cache.clear()
        self._cache[key] = result
        return result

    def _evaluate(self, assignment: Assignment) -> Evaluation | None:
        ids = [i for day in assignment for i in day]
        index = self.lodging_index
        total = sum((self.prices[i].total for i in ids), Decimal(0)) + self.stays[index]
        total = total.quantize(_CENT, ROUND_HALF_UP)
        if total > self.caps[index]:
            return None
        schedules: list[DaySchedule] = []
        for day_index, day_ids in enumerate(assignment):
            schedule = self._schedule(day_index, day_ids)
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
                lodging=self.lodging_q[index],
                params=self.params,
            )
            for person in self.people
        )
        objective = group_objective(
            [
                PersonOutcome(
                    person, s.welfare, self.floors.get(person.id, person.floor)
                )
                for person, s in zip(self.people, scores, strict=True)
            ],
            days=days,
            places=self.places,
            candidates=self.candidates,
            has_lodging=self.trip.has_lodging,
            requirements=self.requirements,
            matches=self.matches,
            presorted=True,
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
                -round(objective.value, ROUNDING),
                total,
                tuple(sorted(self.sid[i] for i in ids)),
                tuple(
                    (d, self.sid[i])
                    for d, day_ids in enumerate(assignment)
                    for i in day_ids
                ),
                index,
            ),
            index,
        )


def with_places(
    current: Assignment, day: int, add: Sequence[UUID] = (), drop: Sequence[UUID] = ()
) -> Assignment:
    """One day of an assignment with places added and dropped.

    Args:
        current: The assignment.
        day: 0-based day to change.
        add: Places to add to the day.
        drop: Places to take out of the day.

    Returns:
        The new assignment (days stay sorted by id).
    """
    return tuple(
        sorted_ids([*(i for i in ids if i not in drop), *add]) if index == day else ids
        for index, ids in enumerate(current)
    )


def _adds(current: Assignment, unplaced: Sequence[UUID]) -> list[Assignment]:
    return [
        with_places(current, d, add=(p,)) for p in unplaced for d in range(len(current))
    ]


def _neighbours(
    current: Assignment, unplaced: Sequence[UUID], must: frozenset[UUID]
) -> list[Assignment]:
    # Fixed order: add, remove, replace, move between days, swap a pair.
    moves = _adds(current, unplaced)
    for d, ids in enumerate(current):
        for p in ids:
            if p in must:
                continue
            moves.append(with_places(current, d, drop=(p,)))
            moves.extend(with_places(current, d, add=(b,), drop=(p,)) for b in unplaced)
    for d, ids in enumerate(current):
        for p in ids:
            moves.extend(
                with_places(with_places(current, d, drop=(p,)), d2, add=(p,))
                for d2 in range(len(current))
                if d2 != d
            )
    for (d1, first), (d2, second) in combinations(enumerate(current), 2):
        for a in first:
            for b in second:
                moved = with_places(current, d1, add=(b,), drop=(a,))
                moves.append(with_places(moved, d2, add=(a,), drop=(b,)))
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
                        with_places(current.assignment, d, add=(must[index],))
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
    start: Assignment | None = None,
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
        start: A feasible plan to improve instead of the empty one (the CP-SAT
            solver passes its choice here to polish it); ignored when it is
            infeasible for the hard constraints.

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
    must = sorted_ids([m for m in data.must if m in evaluator.places])
    limit = max_evaluations or max(
        DEFAULT_MAX_EVALUATIONS,
        EVALUATIONS_PER_SLOT * len(evaluator.candidates) * len(evaluator.windows),
    )
    best, steps, exhausted = _search_with_lodging(evaluator, must, limit, start)
    if evaluator.over_cap[best.lodging_index]:
        conflicts.append((SolverConflict.LODGING_OVER_CAP, None))
    placed = {i for ids in best.assignment for i in ids}
    conflicts.extend(
        (SolverConflict.MUST_UNPLACEABLE, pid) for pid in must if pid not in placed
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return _result(evaluator, best, conflicts, steps, elapsed_ms, exhausted=exhausted)


def _search_with_lodging(
    evaluator: PlanEvaluator,
    must: Sequence[UUID],
    max_evaluations: int,
    given: Assignment | None = None,
) -> tuple[Evaluation, int, bool]:
    """Search the places for every lodging plan and keep the best.

    Each lodging plan changes the cost and the lodging satisfaction, so a base
    that does not fit the places of another one may fit its own (a cheaper day
    plan). The schedule cache is shared, so only the scoring is repeated. A plan
    whose lodging alone breaks the cap is searched only when none fits.

    Args:
        evaluator: The evaluator.
        must: The "must" places that passed E0.
        max_evaluations: Work limit of each search.
        given: A feasible plan to improve instead of the empty one (tried with
            every lodging plan it is feasible for).

    Returns:
        The best evaluation (it knows its lodging plan), the improving steps and
        whether the work limit ended any search.
    """
    indices = list(range(len(evaluator.lodging_plans) or 1))
    fitting = [i for i in indices if not evaluator.over_cap[i]]
    steps = 0
    exhausted = False
    best: Evaluation | None = None
    for index in fitting or indices:
        evaluator.choose_lodging(index)
        found, more, hit = _search(
            evaluator, must, evaluator.evaluations + max_evaluations, given
        )
        steps += more
        exhausted = exhausted or hit
        if best is None or found.key < best.key:
            best = found
    assert best is not None  # ruff: ignore[assert] there is at least one plan
    evaluator.choose_lodging(best.lodging_index)
    return best, steps, exhausted


def _search(
    evaluator: PlanEvaluator,
    must: Sequence[UUID],
    max_evaluations: int,
    given: Assignment | None = None,
) -> tuple[Evaluation, int, bool]:
    # Must places first, then adds only, then the whole neighbourhood until stuck.
    empty: Assignment = tuple(() for _ in evaluator.windows)
    start = evaluator.evaluate(empty)
    if start is None:  # pragma: no cover - an empty plan has no hard constraint
        msg = "The empty plan must be feasible"
        raise RuntimeError(msg)
    improved = None if given is None else evaluator.evaluate(given)
    current = improved or _place_musts(evaluator, start, must)
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
        lodging=evaluator.stays[best.lodging_index]
        if evaluator.lodging_plans
        else None,
        params=evaluator.params,
    )
    chosen = (
        evaluator.lodging_plans[best.lodging_index].nights
        if evaluator.lodging_plans
        else ()
    )
    digest = plan_hash(
        [
            (day, [(v.place_id, v.start, v.end) for v in s.visits])
            for day, s in zip(trip.days, best.schedules, strict=True)
        ],
        [str(o.place_id) for o in chosen],
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
        lodging=chosen,
        lodging_delta=_delta(evaluator, best),
    )


def _delta(evaluator: PlanEvaluator, best: Evaluation) -> LodgingDelta | None:
    # Exceptional nights: the cost and the points they add over the plain base.
    if not evaluator.lodging_plans:
        return None
    nights = evaluator.lodging_plans[best.lodging_index].nights
    base = nights[0]
    odd = tuple(i for i, o in enumerate(nights, start=1) if o.place_id != base.place_id)
    if not odd:
        return None
    plain = next(
        i
        for i, plan in enumerate(evaluator.lodging_plans)
        if all(o.place_id == base.place_id for o in plan.nights)
    )
    evaluator.choose_lodging(plain)
    without = evaluator.evaluate(best.assignment)
    evaluator.choose_lodging(best.lodging_index)
    if without is None:  # the plain base breaks the cap: no comparison
        return None
    return LodgingDelta(
        nights=odd,
        extra_cost=best.cost - without.cost,
        extra_points=tuple(
            (a.person_id, a.welfare - b.welfare)
            for a, b in zip(best.scores, without.scores, strict=True)
        ),
    )


class Solver(Protocol):
    """The interface of every solver: ``solve`` and the CP-SAT solver both fit."""

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
        """Compute the plan that maximises ``J`` under the hard constraints."""
        ...


__all__ = [
    "DEFAULT_MAX_EVALUATIONS",
    "SOLVER_NAME",
    "Assignment",
    "Evaluation",
    "PlanEvaluator",
    "PlanResult",
    "PlannedDay",
    "Solver",
    "SolverConflict",
    "Telemetry",
    "solve",
]
