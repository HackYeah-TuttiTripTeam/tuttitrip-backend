"""Default importance pool by age group.

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
