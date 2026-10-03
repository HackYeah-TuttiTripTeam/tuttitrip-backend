"""Default importance pool by age group and renormalisation to active domains.

The pool is ten points over lodging, food, attractions, pace and cost. The
defaults are a starting point (uncalibrated, like the rest of the spec's
parameters): small children care about pace and a place to sleep, teenagers
about things to do, seniors about pace.
"""

from tuttitrip.profiles.preferences.schemas import ImportanceDomain
from tuttitrip.profiles.schemas import AgeGroup

_L, _F, _A, _P, _C = (
    ImportanceDomain.LODGING,
    ImportanceDomain.FOOD,
    ImportanceDomain.ATTRACTIONS,
    ImportanceDomain.PACE,
    ImportanceDomain.COST,
)

DEFAULT_POOLS: dict[AgeGroup, dict[ImportanceDomain, int]] = {
    AgeGroup.TODDLER: {_L: 3, _F: 1, _A: 1, _P: 4, _C: 1},
    AgeGroup.CHILD: {_L: 1, _F: 2, _A: 3, _P: 3, _C: 1},
    AgeGroup.TEEN: {_L: 1, _F: 2, _A: 4, _P: 1, _C: 2},
    AgeGroup.ADULT: {_L: 2, _F: 2, _A: 3, _P: 1, _C: 2},
    AgeGroup.SENIOR: {_L: 2, _F: 2, _A: 2, _P: 3, _C: 1},
}


def default_pool(group: AgeGroup) -> dict[ImportanceDomain, int]:
    """Importance pool a person of this age group starts with.

    Args:
        group: Age group of the person.

    Returns:
        Points per domain, adding up to 10.
    """
    return dict(DEFAULT_POOLS[group])


def renormalize(
    pool: dict[ImportanceDomain, int], active: frozenset[ImportanceDomain]
) -> dict[ImportanceDomain, float]:
    """Spread the pool over the active domains only (spec section 2).

    Args:
        pool: Points per domain.
        active: Domains in play (``lodging`` only when the trip has stays).

    Returns:
        ``a_ij`` per active domain, summing to 1; an even split when every
        active domain has 0 points.
    """
    kept = {d: p for d, p in pool.items() if d in active}
    total = sum(kept.values())
    if not kept:
        return {}
    if total == 0:
        return {d: 1 / len(kept) for d in kept}
    return {d: p / total for d, p in kept.items()}
