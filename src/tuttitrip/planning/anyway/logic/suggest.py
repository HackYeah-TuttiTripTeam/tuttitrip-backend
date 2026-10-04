"""Choice of the daily "anyway" suggestion (backend#100; EXTENSION, outside v1.0).

docs/algorytm.md has no such rule; the cost is counted like a host override
(backend#52): the group plan with the place as a "must" minus the plan without
it. The candidates are the places with the verdict "iconic, but not yours" (the
catalog marks them iconic or a unique experience, and the group's opinion
``V_p`` is low but not hostile). They are tried best opinion first, ties by id,
at most ``CANDIDATES_PER_DAY`` per day of the trip, so one answer is always
the same for the same data. A candidate that the solver cannot place, or that
lands on a day that already has a suggestion or was rejected for it, is skipped.
Pure.
"""

from collections.abc import Collection, Sequence
from uuid import UUID

from tuttitrip.planning.anyway.logic.constants import CANDIDATES_PER_DAY, TEMPLATE
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.plan_group import GroupPlan, plan_group
from tuttitrip.planning.logic.solver import Solver, solve
from tuttitrip.planning.plans.schemas import (
    AnywayEffects,
    AnywaySuggestion,
    PlanVerdict,
    VerdictKind,
)
from tuttitrip.planning.schemas import PlanningInput


def _day_of(plan: GroupPlan, place_id: UUID) -> int | None:
    for index, day in enumerate(plan.plan.days, start=1):
        if any(v.place_id == place_id for v in day.schedule.visits):
            return index
    return None


def _minutes(plan: GroupPlan) -> int:
    return sum(d.schedule.active_min for d in plan.plan.days)


def _candidates(verdicts: Sequence[PlanVerdict]) -> list[PlanVerdict]:
    iconic = [v for v in verdicts if v.verdict is VerdictKind.ICONIC_NOT_YOURS]
    return sorted(iconic, key=lambda v: (-(v.v_p or 0.0), str(v.place_id)))


def _effects(base: GroupPlan, changed: GroupPlan) -> AnywayEffects:
    return AnywayEffects(
        d_min_r=changed.min_r - base.min_r,
        d_cost=changed.plan.cost.total - base.plan.cost.total,
        d_minutes=_minutes(changed) - _minutes(base),
    )


def template_text(
    suggestion_day: int, v_p: float, effects: AnywayEffects, currency: str
) -> str:
    """The justification written from the numbers.

    Args:
        suggestion_day: 1-based day the place lands on.
        v_p: Weighted opinion of the group.
        effects: Change of cost, time and ``min r``.
        currency: Trip currency code.

    Returns:
        A Polish sentence pair with the numbers.
    """
    return TEMPLATE.format(
        v_p=v_p,
        day=suggestion_day,
        d_cost=effects.d_cost,
        currency=currency,
        d_minutes=effects.d_minutes,
        d_min_r=effects.d_min_r,
    )


def suggest(  # ruff: ignore[too-many-arguments] the plan, its input and the exclusions
    data: PlanningInput,
    chosen: GroupPlan,
    verdicts: Sequence[PlanVerdict],
    *,
    alpha: float = 1.0,
    rejected: Collection[tuple[int, UUID]] = (),
    params: AlgorithmParams = DEFAULT_PARAMS,
    solver: Solver = solve,
) -> tuple[AnywaySuggestion, ...]:
    """At most one suggestion per day, with the cost of adding it.

    Args:
        data: The planning input the plan was computed from.
        chosen: The group plan (with ``u*``).
        verdicts: Verdicts of the candidate places.
        alpha: The fairness slider the plan was computed with.
        rejected: (day, place) pairs the host said no to.
        params: Algorithm parameters.
        solver: The solver of the plan (the same one runs the plan with the place).

    Returns:
        The suggestions in day order; empty when there is no candidate.
    """
    days = len(chosen.plan.days)
    reference = {r.person_id: r.u_star for r in chosen.people}
    in_plan = set(chosen.plan.place_ids)
    names = {p.id: p.name for p in data.places}
    found: dict[int, AnywaySuggestion] = {}
    # A place the host rejected on any day does not take a run of the solver.
    refused = {place for _, place in rejected}
    pool = [
        v
        for v in _candidates(verdicts)
        if v.place_id not in in_plan and v.place_id not in refused
    ]
    for verdict in pool[: days * CANDIDATES_PER_DAY]:
        if len(found) == days:
            break
        changed = data.model_copy(
            update={"must": data.must | {verdict.place_id}},
        )
        with_place = plan_group(
            changed, params, alpha=alpha, u_star=reference, solver=solver
        )
        day = _day_of(with_place, verdict.place_id)
        if day is None or day in found or (day, verdict.place_id) in rejected:
            continue
        effects = _effects(chosen, with_place)
        v_p = verdict.v_p if verdict.v_p is not None else 0.0
        found[day] = AnywaySuggestion(
            place_id=verdict.place_id,
            name=names[verdict.place_id],
            day=day,
            v_p=v_p,
            effects=effects,
            justification=template_text(day, v_p, effects, data.trip.currency),
            justification_source="template",
        )
    return tuple(found[d] for d in sorted(found))
