"""Group goal ``J(P) = W(P) - 1000 * V(P)`` and the tie-break (E5).

```
J(P) = W(P) - 1000 * V(P)         maximise under the hard constraints of E0
tie: (J, lower cost, lexicographic by place ids)
```

Floors, own places and tag minima are soft with a penalty of 1000, so a plan
always exists and the misses are reported. People are processed in id order, so
the result does not depend on the order of the input; ``J`` is rounded to
``ROUNDING`` places in the comparison key so that float noise cannot break a tie.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.fairness.logic.violations import (
    PersonViolation,
    person_violation,
)
from tuttitrip.planning.fairness.logic.welfare import welfare
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.schemas import DayPlan, PlanningPerson

ROUNDING = 9
"""Decimal places of ``J`` in the comparison key."""


@dataclass(frozen=True, slots=True)
class PersonOutcome:
    """A person's welfare under the plan and the floor they may demand."""

    person: PlanningPerson
    utility: float
    floor_eff: float


@dataclass(frozen=True, slots=True)
class GroupObjective:
    """``J`` with its parts."""

    welfare: float
    violation: float
    value: float
    violations: tuple[PersonViolation, ...]
    """Per person, in id order."""


def group_objective(  # ruff: ignore[too-many-arguments] the whole input of J
    outcomes: Sequence[PersonOutcome],
    *,
    days: Sequence[DayPlan],
    places: Mapping[UUID, PlaceRead],
    candidates: Sequence[PlaceRead],
    alpha: float = 1.0,
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> GroupObjective:
    """``J(P)`` of a plan.

    Args:
        outcomes: Each person's ``u_i`` and ``f_i^eff`` under the plan.
        days: The days of the plan.
        places: Places by id.
        candidates: Places that passed E0.
        alpha: Fairness slider in 0 to 3.
        params: Algorithm parameters (``violation_penalty``).

    Returns:
        ``W``, ``V``, ``J`` and the violations per person.
    """
    ordered = sorted(outcomes, key=lambda o: str(o.person.id))
    w = welfare(((o.utility, o.person.weight) for o in ordered), alpha)
    violations = tuple(
        person_violation(
            o.person,
            utility=o.utility,
            floor_eff=o.floor_eff,
            days=days,
            places=places,
            candidates=candidates,
            params=params,
        )
        for o in ordered
    )
    v = math.fsum(item.total for item in violations)
    return GroupObjective(w, v, w - params.violation_penalty * v, violations)


def rank_key(
    objective: GroupObjective, cost: Decimal, place_ids: Sequence[UUID]
) -> tuple[float, Decimal, tuple[str, ...]]:
    """Comparison key of a plan: the smallest key is the best plan.

    Args:
        objective: ``J`` of the plan.
        cost: ``c(P)``.
        place_ids: The places of the plan.

    Returns:
        ``(-J, cost, sorted place ids)``: higher ``J``, then lower cost, then
        the lexicographically smaller list of ids.
    """
    return (
        -round(objective.value, ROUNDING),
        cost,
        tuple(sorted(str(pid) for pid in place_ids)),
    )
