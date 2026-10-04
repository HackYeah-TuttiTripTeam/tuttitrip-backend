"""Utility of a place for a person, without cost (docs/algorytm.md, E1).

```
m = (1 - rho) * cos(I, T_p) + rho * (v + 1) / 2    rho = 0.7 with a vote, else cos
e = 0.6 * min(1, d_p / s) + 0.2 * stairs_p * sens + 0.2 * min(1, queue_p / patience)
lambda_m = (a_dom(p) + 0.1) / Z      lambda_e = (a_pace + 0.1) / Z
u = min(100, 100 * (m + eps)^lambda_m * (1 - e + eps)^lambda_e)
```

Cost is deliberately absent: only E2 and E6 see the price, so it is not counted
twice. ``T_p`` is the 0/1 vector of the place's tags. No data (no interests or
no tags, and no vote) gives ``m = 0.5``. A vote is a key in ``person.votes``,
including the neutral 0; a missing key is no vote. When there is a vote but no
profile, the profile part is the neutral 0.5. Pure, standard library only.
"""

import math

from tuttitrip.places.schemas import PlaceCategory, PlaceRead
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.schemas import PlaceExplain, PlanningPerson
from tuttitrip.profiles.preferences.schemas import ImportanceDomain

# Nightlife is not food: bars are attractions in the pool (spec has two place domains).
_FOOD = frozenset({PlaceCategory.RESTAURANT, PlaceCategory.CAFE})
_MAX_UTILITY = 100.0


def place_domain(place: PlaceRead) -> ImportanceDomain:
    """Pool domain a place belongs to (``dom(p)``): food or attractions.

    Args:
        place: The place.

    Returns:
        ``food`` for restaurants and cafes, ``attractions`` for everything else.
    """
    if place.category in _FOOD:
        return ImportanceDomain.FOOD
    return ImportanceDomain.ATTRACTIONS


def interest_cosine(person: PlanningPerson, place: PlaceRead) -> float | None:
    """Cosine of the interest profile and the place's tags.

    Args:
        person: The person.
        place: The place.

    Returns:
        The cosine in 0 to 1, or None when the profile or the tags are empty.
    """
    tags = set(place.tags)
    norm = math.sqrt(math.fsum(v * v for v in person.interests.values()))
    if not tags or norm == 0:
        return None
    dot = math.fsum(person.interests.get(tag, 0.0) for tag in tags)
    # Rounding can push the ratio a hair above 1 (six tags at 0.1).
    return min(1.0, dot / (norm * math.sqrt(len(tags))))


def match(
    person: PlanningPerson,
    place: PlaceRead,
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> float:
    """Fit of the place to the person, ``m_ip`` in 0 to 1.

    Args:
        person: The person.
        place: The place.
        params: Algorithm parameters.

    Returns:
        ``(1 - rho) * cos + rho * (v + 1) / 2`` with a vote, ``cos`` without,
        ``params.no_data_match`` when there is no data at all.
    """
    cos = interest_cosine(person, place)
    vote = person.votes.get(place.id)
    if vote is None:
        return params.no_data_match if cos is None else cos
    rho = params.vote_weight
    profile = params.no_data_match if cos is None else cos
    return (1 - rho) * profile + rho * (vote + 1) / 2


def effort(
    person: PlanningPerson,
    place: PlaceRead,
) -> float:
    """Effort of the place for the person, ``e_ip`` in 0 to 1.

    Args:
        person: The person.
        place: The place.

    Returns:
        Weighted distance, stairs and queue burden. A person with zero queue
        patience counts any queue as the full burden.
    """
    distance = min(1.0, place.segment_km / person.segment_km)
    # Unknown stairs add no burden here (E0 already screens the strict cases).
    stairs = (place.stairs or 0.0) * person.stairs_sensitivity
    if person.queue_patience_min > 0:
        queue = min(1.0, place.queue_min / person.queue_patience_min)
    else:
        queue = 1.0 if place.queue_min > 0 else 0.0
    return 0.6 * distance + 0.2 * stairs + 0.2 * queue


def exponents(
    person: PlanningPerson,
    place: PlaceRead,
    *,
    has_lodging: bool,
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> tuple[float, float]:
    """Exponents ``(lambda_m, lambda_e)`` of E1, summing to 1.

    The pool is first renormalised to the active domains (lodging only when the
    trip has nights), so a one-day trip needs no special case.

    Args:
        person: The person.
        place: The place.
        has_lodging: Whether the lodging domain is active.
        params: Algorithm parameters.

    Returns:
        ``((a_dom + 0.1) / Z, (a_pace + 0.1) / Z)``.
    """
    pool = person.pool
    points = {
        ImportanceDomain.LODGING: pool.lodging if has_lodging else 0,
        ImportanceDomain.FOOD: pool.food,
        ImportanceDomain.ATTRACTIONS: pool.attractions,
        ImportanceDomain.PACE: pool.pace,
        ImportanceDomain.COST: pool.cost,
    }
    total = sum(points.values())
    # total is 0 when the whole pool sits on a lodging domain that is not active.
    share = {d: (p / total if total else 0.0) for d, p in points.items()}
    lam_m = share[place_domain(place)] + params.lambda_floor
    lam_e = share[ImportanceDomain.PACE] + params.lambda_floor
    norm = lam_m + lam_e
    return lam_m / norm, lam_e / norm


def utility(
    person: PlanningPerson,
    place: PlaceRead,
    *,
    has_lodging: bool,
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> float:
    """Utility of the place for the person, ``u_ip`` in 0 to 100, without cost.

    Args:
        person: The person.
        place: The place.
        has_lodging: Whether the lodging domain is active.
        params: Algorithm parameters.

    Returns:
        ``min(100, 100 * (m + eps)^lambda_m * (1 - e + eps)^lambda_e)``.
    """
    return _scores(person, place, has_lodging, params)[2]


def _scores(
    person: PlanningPerson,
    place: PlaceRead,
    has_lodging: bool,  # ruff: ignore[boolean-type-hint-positional-argument] hot-loop helper, called positionally
    params: AlgorithmParams,
) -> tuple[float, float, float]:
    # (m, e, u) in one pass for the solver's hot loop; explain() wraps it.
    m = match(person, place, params)
    e = effort(person, place)
    lam_m, lam_e = exponents(person, place, has_lodging=has_lodging, params=params)
    eps = params.epsilon
    u = _MAX_UTILITY * math.pow(m + eps, lam_m) * math.pow(1 - e + eps, lam_e)
    return m, e, min(_MAX_UTILITY, u)


def explain(
    person: PlanningPerson,
    place: PlaceRead,
    *,
    has_lodging: bool,
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> PlaceExplain:
    """Card "why this place" for one person (section 10).

    Args:
        person: The person.
        place: The place.
        has_lodging: Whether the lodging domain is active.
        params: Algorithm parameters.

    Returns:
        Match, effort and utility of the place for the person.
    """
    m, e, u = _scores(person, place, has_lodging, params)
    return PlaceExplain(
        person_id=person.id,
        place_id=place.id,
        match=m,
        effort=e,
        utility=u,
    )
