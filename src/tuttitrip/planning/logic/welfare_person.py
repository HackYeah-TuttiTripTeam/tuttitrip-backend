"""Welfare of a person, ``u_i(P)``, the weighted geometric mean of domains (E3).

```
u_i(P) = product_j (1 + q_ij)^a_ij - 1          0 <= u_i <= 100
```

The pool ``a_ij`` is renormalised to the active domains first (lodging only
when the trip has nights). A domain with ``a_ij = 0`` does not affect the
result. A pool whose points all sit on an inactive domain cannot be
renormalised (the spec is silent); it falls back to equal shares of the active
domains so a person is never worth nothing by accident. Computed as
``expm1(sum a_ij * ln(1 + q_ij))``, which is the same number. Results are
rounded to four places at the module boundary so plan hashes stay stable.
"""

import math
from collections.abc import Mapping

from tuttitrip.planning.schemas import DomainScores, PlanningPerson
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
