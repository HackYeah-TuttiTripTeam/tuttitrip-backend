"""Welfare of a person, ``u_i(P)``, the weighted geometric mean of domains (E3).

```
u_i(P) = product_j (1 + q_ij)^a_ij - 1          0 <= u_i <= 100
```

The pool ``a_ij`` is renormalised to the active domains first (lodging only
when the trip has nights). A domain with ``a_ij = 0`` does not affect the
result. A pool whose points all sit on an inactive domain cannot be
renormalised (the spec is silent); it falls back to equal shares of the active
domains so a person is never worth nothing by accident. This is the
limit of the ``lambda_floor`` idea of E1, where an empty pool gives even shares.
Computed as
``expm1(sum a_ij * ln(1 + q_ij))``, which is the same number. Results are
rounded to four places at the module boundary so plan hashes stay stable.
"""

import math
from collections.abc import Mapping, Sequence
from decimal import Decimal
from uuid import UUID

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.logic.domains import (
    RequirementOutcome,
    attractions_score,
    cost_score,
    food_score,
    lodging_score,
    pace_score,
)
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.schemas import (
    DayPlan,
    DomainScores,
    PlanningPerson,
    PlanningTrip,
)
from tuttitrip.profiles.preferences.schemas import ImportanceDomain

ROUNDING = 4
"""Decimal places of every published score."""


def renormalised_pool(
    person: PlanningPerson, *, has_lodging: bool
) -> dict[ImportanceDomain, float]:
    """Shares ``a_ij`` over the active domains, adding up to 1.

    Args:
        person: Gives the pool.
        has_lodging: Whether the lodging domain is active.

    Returns:
        Domain to share, only for active domains.
    """
    pool = person.pool
    points = {
        ImportanceDomain.FOOD: pool.food,
        ImportanceDomain.ATTRACTIONS: pool.attractions,
        ImportanceDomain.PACE: pool.pace,
        ImportanceDomain.COST: pool.cost,
    }
    if has_lodging:
        points[ImportanceDomain.LODGING] = pool.lodging
    total = sum(points.values())
    if total == 0:
        return dict.fromkeys(points, 1 / len(points))
    return {domain: p / total for domain, p in points.items()}


def welfare(
    person: PlanningPerson,
    scores: Mapping[ImportanceDomain, float],
    *,
    has_lodging: bool,
) -> float:
    """Welfare ``u_i`` from the domain scores.

    Args:
        person: Gives the pool.
        scores: ``q_ij`` by domain; the lodging entry is ignored without nights.
        has_lodging: Whether the lodging domain is active.

    Returns:
        ``product_j (1 + q_ij)^a_ij - 1`` in 0 to 100, unrounded.
    """
    shares = renormalised_pool(person, has_lodging=has_lodging)
    value = math.expm1(
        math.fsum(a * math.log1p(scores[domain]) for domain, a in shares.items())
    )
    return min(100.0, max(0.0, value))  # rounding can leave 100.00000000000004


def domain_scores(
    person: PlanningPerson,
    scores: Mapping[ImportanceDomain, float],
    *,
    has_lodging: bool,
) -> DomainScores:
    """The published result for one person: the five ``q_ij`` and ``u_i``.

    Args:
        person: Gives the id and the pool.
        scores: ``q_ij`` by domain (lodging may be missing without nights).
        has_lodging: Whether the lodging domain is active.

    Returns:
        ``DomainScores`` rounded to four places; lodging is None when it does
        not apply.
    """
    lodging = scores[ImportanceDomain.LODGING] if has_lodging else None
    return DomainScores(
        person_id=person.id,
        lodging=None if lodging is None else round(lodging, ROUNDING),
        food=round(scores[ImportanceDomain.FOOD], ROUNDING),
        attractions=round(scores[ImportanceDomain.ATTRACTIONS], ROUNDING),
        pace=round(scores[ImportanceDomain.PACE], ROUNDING),
        cost=round(scores[ImportanceDomain.COST], ROUNDING),
        welfare=round(welfare(person, scores, has_lodging=has_lodging), ROUNDING),
    )


def person_scores(  # ruff: ignore[too-many-arguments] the whole input of E2 and E3
    person: PlanningPerson,
    days: Sequence[DayPlan],
    places: Mapping[UUID, PlaceRead],
    utilities: Mapping[UUID, float],
    *,
    trip: PlanningTrip,
    cost: Decimal,
    lodging: Sequence[RequirementOutcome] | float | None,
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> DomainScores:
    """E2 and E3 for one person and one plan.

    Whether the lodging domain is active is read from ``trip.has_lodging`` only.

    Args:
        person: The person.
        days: The days of the plan (all days of the trip).
        places: Places by id.
        utilities: ``u_ip`` of this person by place id.
        trip: Budget, whether there are nights.
        cost: ``c(P)`` of the plan.
        lodging: The requirements checked against the chosen offer, or the
            lodging satisfaction ``q`` itself (the mean over nights when the
            nights use different bases); given exactly when the trip has nights.
        params: Algorithm parameters.

    Returns:
        The five ``q_ij`` and the welfare ``u_i``.

    Raises:
        ValueError: When ``lodging`` and ``trip.has_lodging`` disagree.
    """
    if (lodging is not None) != trip.has_lodging:
        msg = "lodging outcomes must be given exactly when the trip has nights"
        raise ValueError(msg)
    q = {
        ImportanceDomain.FOOD: food_score(days, places, utilities, params),
        ImportanceDomain.ATTRACTIONS: attractions_score(
            days, places, utilities, params
        ),
        ImportanceDomain.PACE: pace_score(days, person),
        ImportanceDomain.COST: cost_score(cost, trip, params),
    }
    if isinstance(lodging, int | float):
        q[ImportanceDomain.LODGING] = lodging
    elif lodging is not None:
        q[ImportanceDomain.LODGING] = lodging_score(lodging, params)
    return domain_scores(person, q, has_lodging=trip.has_lodging)
