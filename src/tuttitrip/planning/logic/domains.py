"""Satisfaction of a person in each domain, ``q_ij(P)`` in 0 to 100 (E2).

```
attractions: (1/D) sum_d 100 (1 - exp(-0.6 sum_{p in A_d} (tau_p / 90) u_ip / 100))
food:        (1/D) sum_d 100 (1 - exp(-1.2 sum_{p in F_d} u_ip / 100))
pace:        100 (1 - min(1, (1/D) sum_d [(L_d - D_i)+ / 2D_i + (A_d - A_i)+ / 2A_i]))
cost:        100 up to B_od, linearly to 60 at B_do, linearly to 0 at B_max
lodging:     100 S_h,  S_h = product(hard) * mean(soft)  (met 1, unconf. 0.4, unmet 0)
```

Saturation is computed per day and only then averaged, so the fifth attraction
of one day adds less than the first one of an empty day. ``A_d`` and ``F_d`` are
the day's places in the attractions and food domains (``place_domain``); ``D`` is
the number of days of the trip, empty days included. Pure, standard library only.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from tuttitrip.accommodation.schemas import RequirementStatus
from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.utility import place_domain
from tuttitrip.planning.schemas import DayPlan, PlanningPerson, PlanningTrip
from tuttitrip.profiles.preferences.schemas import ImportanceDomain

_FULL = 100.0


@dataclass(frozen=True, slots=True)
class RequirementOutcome:
    """A lodging requirement as checked against the chosen offer."""

    hard: bool
    status: RequirementStatus


def _saturation(load: float, kappa: float) -> float:
    return _FULL * (1 - math.exp(-kappa * load))


def _day_average(
    days: Sequence[DayPlan],
    places: Mapping[UUID, PlaceRead],
    domain: ImportanceDomain,
    weight_of: Mapping[UUID, float],
    kappa: float,
) -> float:
    # Mean over all days of the saturated load of the day's places in a domain.
    per_day = [
        _saturation(
            math.fsum(
                weight_of[pid]
                for pid in day.place_ids
                if place_domain(places[pid]) is domain
            ),
            kappa,
        )
        for day in days
    ]
    return math.fsum(per_day) / len(days)


def attractions_score(
    days: Sequence[DayPlan],
    places: Mapping[UUID, PlaceRead],
    utilities: Mapping[UUID, float],
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> float:
    """Satisfaction with attractions, saturating in each day separately.

    Args:
        days: The days of the plan (all days of the trip).
        places: Places by id.
        utilities: ``u_ip`` of this person by place id.
        params: Algorithm parameters.

    Returns:
        ``(1/D) * sum_d 100 * (1 - exp(-kappa * sum (tau_p / tau_ref) * u / 100))``.
    """
    load = {
        pid: places[pid].typical_visit_min / params.tau_ref_min * utilities[pid] / 100
        for day in days
        for pid in day.place_ids
    }
    return _day_average(
        days, places, ImportanceDomain.ATTRACTIONS, load, params.kappa_attractions
    )


def food_score(
    days: Sequence[DayPlan],
    places: Mapping[UUID, PlaceRead],
    utilities: Mapping[UUID, float],
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> float:
    """Satisfaction with food, saturating in each day separately.

    Args:
        days: The days of the plan (all days of the trip).
        places: Places by id.
        utilities: ``u_ip`` of this person by place id.
        params: Algorithm parameters.

    Returns:
        ``(1/D) * sum_d 100 * (1 - exp(-kappa * sum u / 100))``.
    """
    load = {pid: utilities[pid] / 100 for day in days for pid in day.place_ids}
    return _day_average(days, places, ImportanceDomain.FOOD, load, params.kappa_food)


def pace_score(days: Sequence[DayPlan], person: PlanningPerson) -> float:
    """Satisfaction with the pace: how far the days exceed the person's limits.

    Args:
        days: The days of the plan (all days of the trip).
        person: Gives ``D_i`` (daily km) and ``A_i`` (active minutes).

    Returns:
        ``100 * (1 - min(1, mean_d [(L_d - D_i)+ / 2D_i + (A_d - A_i)+ / 2A_i]))``.
    """
    over = [
        max(0.0, day.distance_km - person.daily_km) / (2 * person.daily_km)
        + max(0, day.active_min - person.active_min) / (2 * person.active_min)
        for day in days
    ]
    return _FULL * (1 - min(1.0, math.fsum(over) / len(days)))


def cost_score(
    cost: Decimal, trip: PlanningTrip, params: AlgorithmParams = DEFAULT_PARAMS
) -> float:
    """Satisfaction with the plan's cost.

    Args:
        cost: ``c(P)`` of the plan.
        trip: Gives ``B_od``, ``B_do`` and ``B_max``.
        params: Algorithm parameters (``cost_comfort``, 60).

    Returns:
        100 for ``c <= B_od``, falling linearly to 60 at ``B_do``, then linearly
        to 0 at ``B_max`` (0 above it).
    """
    comfort = params.cost_comfort
    if cost <= trip.budget_from:
        return _FULL
    if cost <= trip.budget_to:
        done = (cost - trip.budget_from) / (trip.budget_to - trip.budget_from)
        return _FULL - (_FULL - comfort) * float(done)
    if cost <= trip.budget_max:
        done = (cost - trip.budget_to) / (trip.budget_max - trip.budget_to)
        return comfort * (1 - float(done))
    return 0.0


def lodging_score(
    outcomes: Sequence[RequirementOutcome],
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> float:
    """Satisfaction with the lodging base.

    Args:
        outcomes: The trip's requirements checked against the chosen offer.
        params: Algorithm parameters (``uncertain_requirement``, 0.4).

    Returns:
        ``100 * S_h``: the product of the hard requirements times the mean of
        the soft ones (met 1, unconfirmed 0.4, unmet 0). No hard requirements
        give a factor 1, no soft ones too.
    """
    points = {
        RequirementStatus.MET: 1.0,
        RequirementStatus.UNCONFIRMED: params.uncertain_requirement,
        RequirementStatus.UNMET: 0.0,
    }
    hard = math.prod(points[o.status] for o in outcomes if o.hard)
    soft = [points[o.status] for o in outcomes if not o.hard]
    return _FULL * hard * (math.fsum(soft) / len(soft) if soft else 1.0)
