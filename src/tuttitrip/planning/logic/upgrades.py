"""Upgrades of a plan that costs less than ``B_od`` (docs/algorytm.md, E2 and E6).

The cost domain of E2 is 100 points up to ``B_od``, so ``J`` does not reward
spending up to the comfortable level; upgrades are a separate step after the
plan. They are the moves of the solver's neighbourhood that use the room below
``B_od``:

```
add      a place from outside the plan, on any day
replace  a place of the plan by a dearer one of the same category, on its day
```

A move counts when the new plan is feasible (the same hard constraints E0 and
the cost cap as the solver, so no veto, block, closed place or exceeded daily
limit), costs at most ``B_od`` and raises ``J``. ``J`` carries the 1000-point
penalty of the floors, so an upgrade never costs somebody a guarantee. Each
upgrade reports its price, the change of ``J`` and the change of ``min r``.
Pure.
"""

from dataclasses import dataclass
from decimal import Decimal
from itertools import product
from uuid import UUID

from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.plan_group import GroupPlan
from tuttitrip.planning.logic.reference import relative_satisfaction
from tuttitrip.planning.logic.solver import Assignment, Evaluation, PlanEvaluator
from tuttitrip.planning.plans.schemas import UpgradeKind
from tuttitrip.planning.schemas import PlanningInput

MAX_UPGRADES = 5
"""How many upgrades a plan lists, best first."""
MAX_UPGRADE_EVALUATIONS = 5000
"""Work limit of the search (evaluated plans), as in the solver."""
_EPS = 1e-9  # J is a float; a rise below this is rounding


@dataclass(frozen=True, slots=True)
class Upgrade:
    """One possible improvement of a plan."""

    kind: UpgradeKind
    place_id: UUID
    """The place the plan gets."""
    replaces_place_id: UUID | None
    """The place it takes out; None for ``add``."""
    day: int
    """1-based day of the change."""
    cost: Decimal
    """``c(P)`` of the upgraded plan (at most ``B_od``)."""
    extra_cost: Decimal
    """Added to ``c(P)``; zero for a free place."""
    d_j: float
    """Rise of ``J``; positive."""
    d_min_r: float
    """Change of ``min r``; negative when the group gets a bit more uneven."""


def _assignment(plan: GroupPlan) -> Assignment:
    return tuple(
        tuple(sorted((v.place_id for v in d.schedule.visits), key=str))
        for d in plan.plan.days
    )


def _swap(current: Assignment, day: int, drop: UUID | None, add: UUID) -> Assignment:
    return tuple(
        tuple(sorted([*(i for i in ids if i != drop), add], key=str))
        if index == day
        else ids
        for index, ids in enumerate(current)
    )


def _min_r(plan: GroupPlan, evaluation: Evaluation, evaluator: PlanEvaluator) -> float:
    reference = {r.person_id: r.u_star for r in plan.people}
    return min(
        relative_satisfaction(s.welfare, reference[p.id], evaluator.params)
        for p, s in zip(evaluator.people, evaluation.scores, strict=True)
    )


def find_upgrades(
    data: PlanningInput,
    plan: GroupPlan,
    params: AlgorithmParams = DEFAULT_PARAMS,
    *,
    alpha: float = 1.0,
) -> tuple[Upgrade, ...]:
    """Upgrades of a plan that costs less than ``B_od``.

    Args:
        data: The input the plan was computed from.
        plan: The group plan (with ``u*`` and the effective floors).
        params: Algorithm parameters.
        alpha: The fairness slider the plan was computed with.

    Returns:
        Up to ``MAX_UPGRADES`` upgrades, the biggest rise of ``J`` first (then the
        cheaper, then by id); empty when the plan is not below ``B_od`` or no
        move raises ``J``.
    """
    budget_from = data.trip.budget_from
    if plan.plan.cost.total >= budget_from:
        return ()
    evaluator = PlanEvaluator(
        data,
        params,
        alpha=alpha,
        floors={r.person_id: r.floor_eff for r in plan.people},
    )
    current = _assignment(plan)
    base = evaluator.evaluate(current)
    if base is None:  # pragma: no cover - the solver produced this plan
        return ()
    placed = {i for ids in current for i in ids}
    moves: list[tuple[UpgradeKind, int, UUID | None, UUID]] = [
        (UpgradeKind.ADD, day, None, q.id)
        for q, day in product(
            (c for c in evaluator.candidates if c.id not in placed),
            range(len(current)),
        )
    ]
    for day, ids in enumerate(current):
        moves.extend(
            (UpgradeKind.REPLACE, day, p, q.id)
            for p in ids
            if p not in data.must and p in evaluator.places
            for q in evaluator.candidates
            if q.id not in placed
            and q.category == evaluator.places[p].category
            and evaluator.prices[q.id].total > evaluator.prices[p].total
        )
    found: list[Upgrade] = []
    for kind, day, drop, add in moves:
        if evaluator.evaluations >= MAX_UPGRADE_EVALUATIONS:
            break
        changed = evaluator.evaluate(_swap(current, day, drop, add))
        if (
            changed is None
            or changed.cost > budget_from
            or changed.objective.value - base.objective.value <= _EPS
        ):
            continue
        found.append(
            Upgrade(
                kind=kind,
                place_id=add,
                replaces_place_id=drop,
                day=day + 1,
                cost=changed.cost,
                extra_cost=changed.cost - base.cost,
                d_j=changed.objective.value - base.objective.value,
                d_min_r=_min_r(plan, changed, evaluator) - plan.min_r,
            )
        )
    found.sort(
        key=lambda u: (-u.d_j, u.extra_cost, str(u.place_id), str(u.replaces_place_id))
    )
    best_day: dict[tuple[UpgradeKind, UUID, UUID | None], Upgrade] = {}
    for upgrade in found:  # the same move on several days: its best day only
        key = (upgrade.kind, upgrade.place_id, upgrade.replaces_place_id)
        best_day.setdefault(key, upgrade)
    return tuple(best_day.values())[:MAX_UPGRADES]
