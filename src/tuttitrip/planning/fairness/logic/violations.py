"""Soft violations ``V(P)`` of the group goal (docs/algorytm.md, E5).

```
V(P) = sum_i [ (f_i^eff - u_i)+ / f_i^eff  +  1/2 * (days without an own place)
               + sum_tag (k - have)+ / k ]
```

* Floor: ``f_i^eff = min(f_i, 0.6 * u*_i)`` (E4); a floor of 0 never counts.
  ``u*_i`` comes with backend#48; until then pass ``f_i``.
* Own place: a day has one for the person when some place has ``m_ip >= 0.6``;
  a day with no places has none.
* Tag minimum: a tag whose pool domain has ``a_i,dom >= theta = 0.4`` demands
  ``k = 1 + floor((a_i,dom - theta) / (1 - theta) * 2)`` places, at most as many
  as the candidates offer. It is counted on the integer pool points with exact
  fractions: in floats ``(0.7 - 0.4) / 0.6 * 2`` is 0.9999..., which would give
  ``k = 1`` instead of 2 for 7 points. ``have`` counts distinct places of the
  plan that carry the tag (a cuisine for food, a place tag for attractions).

The penalty (1000) is applied in ``objective``. Pure, standard library only.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from uuid import UUID

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.utility import match
from tuttitrip.planning.schemas import DayPlan, PlanningPerson
from tuttitrip.profiles.preferences.schemas import POOL_TOTAL, MinTag, MinTagDomain

OWN_PLACE_WEIGHT = 0.5
"""Each day without an own place counts this much in ``V``."""
_TAG_POINTS_PER_EXTRA = 2


@dataclass(frozen=True, slots=True)
class TagShortfall:
    """A tag minimum of one person and how far the plan is from it."""

    tag: MinTag
    required: int
    have: int

    @property
    def term(self) -> float:
        """``(k - have)+ / k``."""
        return max(0, self.required - self.have) / self.required


@dataclass(frozen=True, slots=True)
class PersonViolation:
    """The three kinds of violation of one person."""

    person_id: UUID
    floor_term: float
    own_days_missing: int
    tags: tuple[TagShortfall, ...]

    @property
    def total(self) -> float:
        """This person's share of ``V``."""
        return math.fsum(
            [
                self.floor_term,
                OWN_PLACE_WEIGHT * self.own_days_missing,
                *(t.term for t in self.tags),
            ]
        )


def effective_floor(
    floor: float, u_star: float, params: AlgorithmParams = DEFAULT_PARAMS
) -> float:
    """The floor a person can honestly demand: at most 0.6 of what they get alone.

    Args:
        floor: ``f_i``.
        u_star: ``u*_i``, the person's best welfare alone (E4).
        params: Algorithm parameters (``floor_share``).

    Returns:
        ``min(f_i, 0.6 * u*_i)``.
    """
    return min(floor, params.floor_share * u_star)


def floor_term(utility: float, floor_eff: float) -> float:
    """``(f_eff - u)+ / f_eff``, 0 when there is no floor.

    Args:
        utility: ``u_i``.
        floor_eff: ``f_i^eff``.

    Returns:
        The relative shortfall in 0 to 1.
    """
    if floor_eff <= 0:
        return 0.0
    return max(0.0, floor_eff - utility) / floor_eff


def min_tag_count(points: int, params: AlgorithmParams = DEFAULT_PARAMS) -> int:
    """Number of places a pool domain with ``points`` of 10 demands for a tag.

    Args:
        points: Whole pool points of the tag's domain.
        params: Algorithm parameters (``strong_preference``, theta).

    Returns:
        0 below theta, else ``1 + floor((a - theta) / (1 - theta) * 2)``.
    """
    theta = Fraction(str(params.strong_preference))
    share = Fraction(points, POOL_TOTAL)
    if share < theta:
        return 0
    return 1 + math.floor((share - theta) / (1 - theta) * _TAG_POINTS_PER_EXTRA)


def _carries(place: PlaceRead, minimum: MinTag) -> bool:
    if minimum.domain is MinTagDomain.FOOD:
        return place.cuisine is not None and place.cuisine.value == minimum.tag
    return minimum.tag in {t.value for t in place.tags}


def days_without_own_place(
    person: PlanningPerson,
    days: Sequence[DayPlan],
    places: Mapping[UUID, PlaceRead],
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> int:
    """Days with no place the person likes (``m_ip >= 0.6``).

    Args:
        person: The person.
        days: The days of the plan.
        places: Places by id.
        params: Algorithm parameters (``own_place_match``).

    Returns:
        How many days have none, empty days included.
    """
    return sum(
        not any(
            match(person, places[pid], params) >= params.own_place_match
            for pid in day.place_ids
        )
        for day in days
    )


def tag_shortfalls(
    person: PlanningPerson,
    days: Sequence[DayPlan],
    places: Mapping[UUID, PlaceRead],
    candidates: Sequence[PlaceRead],
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> tuple[TagShortfall, ...]:
    """The person's tag minima that apply and how many places the plan has.

    Args:
        person: The person.
        days: The days of the plan.
        places: Places by id.
        candidates: Places that passed E0 (limit ``k`` by availability).
        params: Algorithm parameters.

    Returns:
        One entry per minimum with ``k >= 1`` after the availability cap.
    """
    chosen = {places[pid].id: places[pid] for day in days for pid in day.place_ids}
    result: list[TagShortfall] = []
    for minimum in person.min_tags:
        points = (
            person.pool.food
            if minimum.domain is MinTagDomain.FOOD
            else person.pool.attractions
        )
        available = sum(_carries(p, minimum) for p in candidates)
        required = min(min_tag_count(points, params), available)
        if required >= 1:
            have = sum(_carries(p, minimum) for p in chosen.values())
            result.append(TagShortfall(minimum, required, have))
    return tuple(result)


def person_violation(  # ruff: ignore[too-many-arguments] the whole input of V
    person: PlanningPerson,
    *,
    utility: float,
    floor_eff: float,
    days: Sequence[DayPlan],
    places: Mapping[UUID, PlaceRead],
    candidates: Sequence[PlaceRead],
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> PersonViolation:
    """The bracket of ``V(P)`` for one person.

    Args:
        person: The person.
        utility: ``u_i`` of the plan.
        floor_eff: ``f_i^eff``.
        days: The days of the plan.
        places: Places by id.
        candidates: Places that passed E0.
        params: Algorithm parameters.

    Returns:
        The three kinds of violation.
    """
    return PersonViolation(
        person_id=person.id,
        floor_term=floor_term(utility, floor_eff),
        own_days_missing=days_without_own_place(person, days, places, params),
        tags=tag_shortfalls(person, days, places, candidates, params),
    )
