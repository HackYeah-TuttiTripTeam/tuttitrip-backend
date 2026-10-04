"""Consent to exceed the budget (docs/algorytm.md, E6 and section 6 "good reason").

```
P_flex   limit B_max = B_do * (1 + flex)        c(P_flex) <= B_do: done, no consent
P_strict limit B_do
P_cheap  limit c(P_flex) - 5% * B_do
consent  (1) somebody with a strong preference (a_i,dom >= theta = 0.4) gains >= 8
             points between P_strict and P_flex, or min r rises by >= 0.05, and
         (2) there is no cheaper alternative: W(P_flex) - W(P_cheap) >= 0.03
kappa    (c_flex - c_strict) / max_i dU_i      currency per point
```

When both hold the result is ``P_flex`` with ``needs_approval`` and ``kappa``, and
``P_strict`` as the alternative; otherwise ``P_strict``. With ``flex = 0`` the
cap is ``B_do``, so there is nothing to approve. All three group plans use the
same ``u*`` (the solo runs are done once). Pure, standard library only.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from tuttitrip.planning.fairness.logic.welfare import welfare
from tuttitrip.planning.logic.domains import RequirementOutcome
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.plan_group import GroupPlan, plan_group
from tuttitrip.planning.schemas import LodgingStay, PlanningInput, PlanningPerson
from tuttitrip.profiles.preferences.schemas import POOL_TOTAL

_CENT = Decimal("0.01")
_EPS = 1e-9  # thresholds are exact in the spec; floats are not


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    """What E6 decided about going over ``B_do``."""

    chosen: GroupPlan
    """``P_flex`` when consent is needed, else the plan within the limit."""
    needs_approval: bool
    over_b_do: Decimal
    """``c(chosen) - B_do`` when positive, else 0."""
    kappa: Decimal | None
    """Price of a point of satisfaction; set exactly when ``needs_approval``."""
    gain_person_id: UUID | None
    gain_points: float | None
    alternative: GroupPlan | None
    """``P_strict``, the alternative to the consent plan."""
    runs: int
    """Group plans computed (1 without a consent question, else 3)."""


def has_strong_preference(
    person: PlanningPerson, *, has_lodging: bool, params: AlgorithmParams
) -> bool:
    """Whether some domain holds at least ``theta`` of the person's active pool.

    Args:
        person: The person.
        has_lodging: Whether the lodging domain is active.
        params: Algorithm parameters (``strong_preference``).

    Returns:
        True for a strong preference (``a_ij >= theta``).
    """
    pool = person.pool
    points = [pool.food, pool.attractions, pool.pace, pool.cost]
    if has_lodging:
        points.append(pool.lodging)
    total = sum(points) or POOL_TOTAL
    return max(points) / total >= params.strong_preference


def _w(people: Sequence[PlanningPerson], plan: GroupPlan, alpha: float) -> float:
    weights = {p.id: p.weight for p in people}
    return welfare(((r.u, weights[r.person_id]) for r in plan.people), alpha)


def _gains(strict: GroupPlan, flex: GroupPlan) -> dict[UUID, float]:
    before = {r.person_id: r.u for r in strict.people}
    return {r.person_id: r.u - before[r.person_id] for r in flex.people}


def decide(  # ruff: ignore[too-many-arguments] the three plans and their context
    data: PlanningInput,
    flex: GroupPlan,
    strict: GroupPlan,
    cheaper: GroupPlan,
    *,
    alpha: float = 1.0,
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> BudgetDecision:
    """Apply the two conditions of a "good reason" to three computed plans.

    Args:
        data: The planning input (people, weights, budget).
        flex: ``P_flex``, over ``B_do``.
        strict: ``P_strict``, within ``B_do``.
        cheaper: ``P_cheap``.
        alpha: The fairness slider used for ``W``.
        params: Algorithm parameters (thresholds 8, 0.05, 0.03).

    Returns:
        The decision with ``kappa`` and the person who gains most.
    """
    people = {p.id: p for p in data.people}
    gains = _gains(strict, flex)
    strong = [
        gain
        for pid, gain in gains.items()
        if has_strong_preference(
            people[pid], has_lodging=data.trip.has_lodging, params=params
        )
    ]
    condition_one = (
        max(strong, default=0.0) >= params.good_reason_points - _EPS
        or flex.min_r - strict.min_r >= params.good_reason_min_r - _EPS
    )
    condition_two = (
        _w(data.people, flex, alpha) - _w(data.people, cheaper, alpha)
        >= params.good_reason_welfare - _EPS
    )
    best_id = min(gains, key=lambda pid: (-gains[pid], str(pid)))
    best_gain = gains[best_id]
    over = max(Decimal(0), flex.plan.cost.total - data.trip.budget_to)
    if condition_one and condition_two and best_gain > 0:
        extra = flex.plan.cost.total - strict.plan.cost.total
        kappa = (extra / Decimal(str(best_gain))).quantize(_CENT, ROUND_HALF_UP)
        return BudgetDecision(
            chosen=flex,
            needs_approval=True,
            over_b_do=over,
            kappa=kappa,
            gain_person_id=best_id,
            gain_points=best_gain,
            alternative=strict,
            runs=3,
        )
    return BudgetDecision(
        chosen=strict,
        needs_approval=False,
        over_b_do=max(Decimal(0), strict.plan.cost.total - data.trip.budget_to),
        kappa=None,
        gain_person_id=None,
        gain_points=None,
        alternative=None,
        runs=3,
    )


def plan_with_consent(  # ruff: ignore[too-many-arguments] the whole input of E6
    data: PlanningInput,
    params: AlgorithmParams = DEFAULT_PARAMS,
    *,
    alpha: float = 1.0,
    lodging: LodgingStay | None = None,
    lodging_outcomes: Sequence[RequirementOutcome] | None = None,
    max_evaluations: int | None = None,
) -> BudgetDecision:
    """Compute the group plan and decide whether it needs the host's consent.

    Args:
        data: The planning input.
        params: Algorithm parameters.
        alpha: The fairness slider.
        lodging: The lodging base, given exactly when the trip has nights.
        lodging_outcomes: The trip's lodging requirements checked against it.
        max_evaluations: Work limit of every run.

    Returns:
        The decision; with a cost within ``B_do`` there is one run and no consent.
    """

    def run(
        cost_cap: Decimal | None = None, u_star: Mapping[UUID, float] | None = None
    ) -> GroupPlan:
        return plan_group(
            data,
            params,
            alpha=alpha,
            lodging=lodging,
            lodging_outcomes=lodging_outcomes,
            max_evaluations=max_evaluations,
            cost_cap=cost_cap,
            u_star=u_star,
        )

    flex = run()
    budget_to = data.trip.budget_to
    if flex.plan.cost.total <= budget_to:
        return BudgetDecision(
            chosen=flex,
            needs_approval=False,
            over_b_do=Decimal(0),
            kappa=None,
            gain_person_id=None,
            gain_points=None,
            alternative=None,
            runs=1,
        )
    reference = {r.person_id: r.u_star for r in flex.people}
    strict = run(budget_to, reference)
    margin = Decimal(str(params.cheaper_margin)) * budget_to
    cheaper = run(flex.plan.cost.total - margin, reference)
    return decide(data, flex, strict, cheaper, alpha=alpha, params=params)
