"""Replan the rest of a day, e.g. in rain (backend#74; EXTENSION, outside v1.0).

docs/algorytm.md (section 9) lists "rain and plan stability" as out of scope of
v1.0. This does not touch E0 to E6: it runs the same evaluator (hard constraints
E0, the group goal ``J`` of E5, the same ``u*`` through the same floors) on one
day, with two changes:

* in rain ``u_ip`` is multiplied by ``0.3 + 0.7 * [indoor]`` (the factors are
  parameters); an unknown ``indoor`` counts as half indoors;
* the objective is ``J_replan = J - 0.2 * changes - 0.001 * shift_minutes``, so a
  replan changes as little as it must (a place added or removed is a change, a
  kept visit that starts later or earlier adds its shift in minutes).

Visits that started before ``as_of`` stay exactly as they were, and everything
new starts at or after ``as_of``. The clock is injected (``as_of``); nothing here
reads the time. Pure and deterministic.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.solver import (
    Assignment,
    Evaluation,
    PlanEvaluator,
    sorted_ids,
    with_places,
)
from tuttitrip.planning.schemas import LodgingStay, PlanningInput


@dataclass(frozen=True, slots=True)
class DayState:
    """The day as it is: its places, in order, with the times they were given."""

    place_ids: tuple[UUID, ...]
    starts: Mapping[UUID, datetime]
    """Start of each visit (timezone-aware, city time)."""


@dataclass(frozen=True, slots=True)
class ReplanResult:
    """The replanned day and what changed."""

    evaluation: Evaluation
    before: Evaluation | None
    """The plan as it was, evaluated with the same factors (None if infeasible)."""
    removed: tuple[UUID, ...]
    added: tuple[UUID, ...]
    shifted: tuple[tuple[UUID, int], ...]
    """Kept visits that moved, with the shift in minutes (positive = later)."""
    j_replan: float
    evaluations: int


def rain_factors(
    places: Sequence[PlaceRead], params: AlgorithmParams = DEFAULT_PARAMS
) -> dict[UUID, float]:
    """``0.3 + 0.7 * [indoor]`` for every place.

    Args:
        places: The candidate places.
        params: Algorithm parameters (``rain_outdoor_factor``, ``rain_indoor_weight``).

    Returns:
        The factor on ``u_ip`` by place id; 1 for indoors, ``0.3`` outdoors and
        half way for an unknown ``indoor``.
    """
    weight = params.rain_indoor_weight
    base = params.rain_outdoor_factor
    return {
        p.id: base + weight * (0.5 if p.indoor is None else float(p.indoor))
        for p in places
    }


def _minutes(delta_seconds: float) -> int:
    return round(delta_seconds / 60)


_Scored = tuple[float, Evaluation, tuple[tuple[UUID, int], ...]]
"""``J_replan``, the evaluation and the kept visits that moved."""


class _Search:
    """The search for the rest of one day; holds the pieces it shares."""

    def __init__(
        self,
        evaluator: PlanEvaluator,
        day: int,
        state: DayState,
        as_of: datetime,
        params: AlgorithmParams,
    ) -> None:
        self.evaluator = evaluator
        self.day = day
        self.as_of = as_of
        self.params = params
        self.state = state
        self.fixed = frozenset(
            pid for pid in state.place_ids if state.starts[pid] < as_of
        )

    def score(self, assignment: Assignment) -> _Scored | None:
        evaluation = self.evaluator.evaluate(assignment)
        if evaluation is None:
            return None
        kept = self.state.starts
        shifts: list[tuple[UUID, int]] = []
        for visit in evaluation.schedules[self.day].visits:
            pid = visit.place_id
            if pid in self.fixed:
                if visit.start != kept[pid]:  # a started visit never moves
                    return None
            elif visit.start < self.as_of:  # nothing new in the past
                return None
            elif pid in kept:
                shift = _minutes((visit.start - kept[pid]).total_seconds())
                if shift:
                    shifts.append((pid, shift))
        before = set(self.state.place_ids)
        after = {v.place_id for v in evaluation.schedules[self.day].visits}
        changes = len(before ^ after)
        penalty = self.params.replan_change_penalty * changes
        penalty += self.params.replan_shift_penalty * sum(abs(m) for _, m in shifts)
        return evaluation.objective.value - penalty, evaluation, tuple(shifts)

    def improve(
        self, chosen: Assignment, best: float, must: frozenset[UUID]
    ) -> (
        tuple[Assignment, tuple[float, Evaluation, tuple[tuple[UUID, int], ...]]] | None
    ):
        """The best strictly better neighbour of the day, or None.

        Moves: drop a place that has not started, add a free place, replace one by
        another. Places of other days are not touched, and a "must" stays.

        Args:
            chosen: The assignment so far.
            best: ``J_replan`` of ``chosen``.
            must: Places that may not be dropped.

        Returns:
            The better assignment with its score, or None at a local optimum.
        """
        day = self.day
        placed = {p for ids in chosen for p in ids}
        rest = [p for p in chosen[day] if p not in self.fixed and p not in must]
        pool = [p.id for p in self.evaluator.candidates if p.id not in placed]
        moves = [with_places(chosen, day, drop=(p,)) for p in rest]
        moves += [with_places(chosen, day, add=(q,)) for q in pool]
        moves += [
            with_places(chosen, day, add=(q,), drop=(p,)) for p in rest for q in pool
        ]
        found = None
        for move in moves:
            scored = self.score(move)
            if (
                scored is not None
                and scored[0] > (found[1] if found else (best, 0))[0] + 1e-12
            ):
                found = (move, scored)
        return found


def replan_rest_of_day(  # ruff: ignore[too-many-arguments] the whole input of a replan
    data: PlanningInput,
    current: Assignment,
    *,
    day: int,
    state: DayState,
    as_of: datetime,
    lodging_nights: Sequence[UUID] = (),
    lodging: LodgingStay | None = None,
    floors: Mapping[UUID, float] | None = None,
    alpha: float = 1.0,
    params: AlgorithmParams = DEFAULT_PARAMS,
    cost_cap: Decimal | None = None,
    rain: bool = True,
) -> ReplanResult:
    """The best rest of the day under rain (or, with ``rain=False``, as it is).

    Args:
        data: The planning input as it is now.
        current: The plan's places per day (places that are no longer allowed
            are dropped and count as changes).
        day: 0-based index of the day to replan.
        state: The day's places with their start times.
        as_of: The moment of the replan, timezone-aware.
        lodging_nights: The base of each night in the plan (to price it alike).
        lodging: A single lodging base instead of the options of ``data``.
        floors: ``f_i^eff`` by person id, as in the plan (E4).
        alpha: The fairness slider of the plan.
        params: Algorithm parameters.
        cost_cap: Hard cost limit; default ``B_max``.
        rain: Apply the rain factors to ``u_ip``.

    Returns:
        The replanned day, what was removed, added and moved, and ``J_replan``.
    """
    evaluator = _evaluator(
        data,
        params,
        alpha=alpha,
        floors=floors,
        cost_cap=cost_cap,
        lodging=lodging,
        rain=rain,
    )
    _choose_lodging(evaluator, lodging_nights)
    allowed = set(evaluator.places)
    start: Assignment = tuple(
        sorted_ids([p for p in ids if p in allowed]) for ids in current
    )
    search = _Search(evaluator, day, state, as_of, params)
    best = search.score(start)
    if best is None:  # the old day no longer works: keep only what already started
        start = with_places(
            start, day, drop=[p for p in start[day] if p not in search.fixed]
        )
        best = search.score(start)
    assert best is not None  # ruff: ignore[assert] the started visits alone are feasible
    chosen = start
    while (found := search.improve(chosen, best[0], frozenset(data.must))) is not None:
        chosen, best = found
    j_replan, evaluation, shifts = best
    old = set(state.place_ids)
    new = {v.place_id for v in evaluation.schedules[day].visits}
    before = evaluator.evaluate(start)
    return ReplanResult(
        evaluation=evaluation,
        before=before,
        removed=sorted_ids(old - new),
        added=sorted_ids(new - old),
        shifted=shifts,
        j_replan=j_replan,
        evaluations=evaluator.evaluations,
    )


def _evaluator(  # ruff: ignore[too-many-arguments] the inputs of the evaluator
    data: PlanningInput,
    params: AlgorithmParams,
    *,
    alpha: float,
    floors: Mapping[UUID, float] | None,
    cost_cap: Decimal | None,
    lodging: LodgingStay | None,
    rain: bool,
) -> PlanEvaluator:
    plain = PlanEvaluator(
        data, params, alpha=alpha, floors=floors, cost_cap=cost_cap, lodging=lodging
    )
    if not rain:
        return plain
    return PlanEvaluator(
        data,
        params,
        alpha=alpha,
        floors=floors,
        cost_cap=cost_cap,
        lodging=lodging,
        utility_factors=rain_factors(plain.candidates, params),
    )


def _choose_lodging(evaluator: PlanEvaluator, nights: Sequence[UUID]) -> None:
    # The lodging plan of the stored plan; the first one if it cannot be found.
    for index, plan in enumerate(evaluator.lodging_plans):
        if tuple(o.place_id for o in plan.nights) == tuple(nights):
            evaluator.choose_lodging(index)
            return
