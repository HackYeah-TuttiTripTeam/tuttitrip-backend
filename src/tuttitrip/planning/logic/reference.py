"""Reference point "what I would get alone" (docs/algorytm.md, E4).

```
u*_i    = max_P u_i(P)                   the same solver, n = 1, share 1/N of the budget
r_i     = min(1, (u_i + s) / (u*_i + s)) s = 10; shown as "x% of your maximum"
f_i^eff = min(f_i, 0.6 * u*_i)           never more than one could get alone
n = 1:  r_i = 1 and f_i^eff = 0
```

The solo run of a person keeps their own vetoes, comfort and tickets, but not the
group's "must" places, and gets ``B_od / N``, ``B_do / N`` and ``B_max / N`` (the
lodging, if any, is also paid 1/N). The runs go one after another in id order, so
the result is deterministic. Pure, standard library only.
"""

from collections.abc import Sequence

from tuttitrip.planning.logic.domains import RequirementOutcome
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.solver import PlanResult, Solver, solve
from tuttitrip.planning.schemas import (
    LodgingStay,
    PlanningInput,
    PlanningPerson,
    PlanningTrip,
)


def solo_input(data: PlanningInput, person: PlanningPerson) -> PlanningInput:
    """The trip of one person with ``1/N`` of the budget.

    Args:
        data: The group's planning input.
        person: The person whose solo plan is wanted.

    Returns:
        The same trip, places and currency for ``person`` alone; budgets divided
        by the group size, no "must".
    """
    share = len(data.people)
    trip: PlanningTrip = data.trip.model_copy(
        update={
            "budget_from": data.trip.budget_from / share,
            "budget_to": data.trip.budget_to / share,
        }
    )
    options = tuple(
        o.model_copy(update={"price_per_night": o.price_per_night / share})
        for o in data.lodgings
    )
    return data.model_copy(
        update={
            "trip": trip,
            "people": (person,),
            "must": frozenset(),
            "lodgings": options,
        }
    )


def solo_lodging(lodging: LodgingStay | None, share: int) -> LodgingStay | None:
    """The person's part of the lodging (``1/N`` of the nightly price).

    Args:
        lodging: The group's lodging base, or None.
        share: Group size ``N``.

    Returns:
        The lodging priced for one person, or None.
    """
    if lodging is None:
        return None
    return lodging.model_copy(
        update={"price_per_night": lodging.price_per_night / share}
    )


def solo_utility(  # ruff: ignore[too-many-arguments] the whole input of a solo run
    data: PlanningInput,
    person: PlanningPerson,
    params: AlgorithmParams = DEFAULT_PARAMS,
    *,
    alpha: float = 1.0,
    lodging: LodgingStay | None = None,
    lodging_outcomes: Sequence[RequirementOutcome] | None = None,
    max_evaluations: int | None = None,
    solver: Solver = solve,
) -> PlanResult:
    """The best plan of one person alone, ``u*_i`` is ``scores[0].welfare``.

    Args:
        data: The group's planning input.
        person: The person.
        params: Algorithm parameters.
        alpha: Fairness slider (no effect for one person).
        lodging: The group's lodging base, or None.
        lodging_outcomes: Requirements checked against it.
        max_evaluations: Work limit of the run; default scales with the instance.
        solver: The solver to run (default: the local search).

    Returns:
        The solo plan.
    """
    return solver(
        solo_input(data, person),
        params,
        alpha=alpha,
        lodging=solo_lodging(lodging, len(data.people)),
        lodging_outcomes=lodging_outcomes,
        floors={person.id: 0.0},
        max_evaluations=max_evaluations,
    )


def relative_satisfaction(
    utility: float, u_star: float, params: AlgorithmParams = DEFAULT_PARAMS
) -> float:
    """``r_i``: the share of one's own maximum, smoothed.

    Args:
        utility: ``u_i`` in the group plan.
        u_star: ``u*_i``.
        params: Algorithm parameters (``smoothing``, s = 10).

    Returns:
        ``min(1, (u + s) / (u* + s))``.
    """
    s = params.smoothing
    return min(1.0, (utility + s) / (u_star + s))
