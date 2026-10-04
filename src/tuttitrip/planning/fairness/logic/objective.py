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
    TagRequirement,
    person_violation,
    tag_requirements,
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
    has_lodging: bool = True,
    requirements: Mapping[UUID, Sequence[TagRequirement]] | None = None,
    matches: Mapping[UUID, Mapping[UUID, float]] | None = None,
    presorted: bool = False,
    alpha: float = 1.0,
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> GroupObjective:
    """``J(P)`` of a plan.

    Args:
        outcomes: Each person's ``u_i`` and ``f_i^eff`` under the plan.
        days: The days of the plan.
        places: Places by id.
        candidates: Places that passed E0.
        has_lodging: Whether the lodging domain is active.
        requirements: Tag minima per person id from ``tag_requirements``; the
            solver computes them once, here they are derived when missing.
        matches: ``m_ip`` by person id and place id, if the caller computed them.
        presorted: The outcomes are already in person id order (saves the sort).
        alpha: Fairness slider in 0 to 3.
        params: Algorithm parameters (``violation_penalty``).

    Returns:
        ``W``, ``V``, ``J`` and the violations per person.
    """
    ordered = (
        list(outcomes)
        if presorted
        else sorted(outcomes, key=lambda o: str(o.person.id))
    )
    alone = len(ordered) == 1  # E4: for n = 1 the floor is 0 (nobody to protect from)
    needs = requirements or {
        o.person.id: tag_requirements(
            o.person, candidates, has_lodging=has_lodging, params=params
        )
        for o in ordered
    }
    w = welfare(((o.utility, o.person.weight) for o in ordered), alpha)
    violations = tuple(
        person_violation(
            o.person,
            utility=o.utility,
            floor_eff=0.0 if alone else o.floor_eff,
            days=days,
            places=places,
            requirements=needs[o.person.id],
            params=params,
            matches=None if matches is None else matches[o.person.id],
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
