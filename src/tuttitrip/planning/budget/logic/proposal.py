"""A cheaper plan for the rest of the trip after a day went over (backend#89).

```
rest      the days after the last overrunning day, with B_od and B_do reduced by
          everything spent so far (``daily_budget.budget_left``)
solver    the same solver and the goal J of E5 as for any plan (``plan_with_consent``),
          so a proposal over B_do goes through the consent of E6 (kappa, P_strict)
compare   the plan's own days of the rest, evaluated on the same input without the
          cost cap: their cost and min r against the proposal's
proposal  only when it costs less than those days
```

The extension that swaps the rest of a day in place (backend#74) is outside version
1.0: a proposal replaces whole days after the overrun. Pure.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from tuttitrip.planning.logic.budget_consent import BudgetDecision, plan_with_consent
from tuttitrip.planning.logic.params import AlgorithmParams
from tuttitrip.planning.logic.reference import relative_satisfaction
from tuttitrip.planning.logic.solver import Assignment, PlanEvaluator, Solver, solve
from tuttitrip.planning.schemas import PlanningInput

NO_CAP = Decimal(10) ** 12
"""Cost cap that never binds, to price a plan the reduced budget no longer allows."""


@dataclass(frozen=True, slots=True)
class PreviousRest:
    """The plan's own days of the rest, priced on the reduced input."""

    cost: Decimal
    min_r: float | None
    """None when the days no longer pass the hard constraints (E0)."""


@dataclass(frozen=True, slots=True)
class RestProposal:
    """The proposal and what it replaces."""

    decision: BudgetDecision
    previous: PreviousRest


def previous_rest(
    data: PlanningInput,
    params: AlgorithmParams,
    assignment: Assignment,
    u_star: Mapping[UUID, float],
    alpha: float,
) -> PreviousRest:
    """Cost and ``min r`` of the plan's own days of the rest.

    Args:
        data: The reduced input of the rest.
        params: Algorithm parameters.
        assignment: The plan's places per remaining day.
        u_star: ``u*`` per person of the proposal's run (the same reference).
        alpha: The fairness slider.

    Returns:
        Their cost and ``min r``; ``min_r`` is None when they fail E0 now.
    """
    evaluator = PlanEvaluator(data, params, alpha=alpha, cost_cap=NO_CAP)
    evaluated = evaluator.evaluate(assignment)
    if evaluated is None:
        ids = [i for day in assignment for i in day if i in evaluator.prices]
        return PreviousRest(
            sum((evaluator.prices[i].total for i in ids), Decimal(0)), None
        )
    if len(data.people) == 1:
        return PreviousRest(evaluated.cost, 1.0)
    rs = [
        relative_satisfaction(score.welfare, u_star[score.person_id], params)
        for score in evaluated.scores
    ]
    return PreviousRest(evaluated.cost, min(rs))


def propose_rest(
    data: PlanningInput,
    params: AlgorithmParams,
    *,
    alpha: float,
    assignment: Assignment,
    solver: Solver = solve,
) -> RestProposal | None:
    """Plan the rest of the trip with the reduced budget and compare it.

    Args:
        data: The reduced input of the rest (``daily_budget.rest_of_trip``).
        params: Algorithm parameters.
        alpha: The fairness slider.
        assignment: The plan's own places per remaining day.
        solver: The solver of every run.

    Returns:
        The proposal, or None when it is not cheaper than the plan's own days.
    """
    decision = plan_with_consent(data, params, alpha=alpha, solver=solver)
    reference = {row.person_id: row.u_star for row in decision.chosen.people}
    previous = previous_rest(data, params, assignment, reference, alpha)
    if decision.chosen.plan.cost.total >= previous.cost:
        return None
    return RestProposal(decision, previous)
