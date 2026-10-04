"""Build the algorithm's input from what the other domains know (pure).

Everything here is a plain function of DTOs, so it is testable without a
database. Choices where the data has gaps:

* No budget set: the trip is priced without a limit (``NO_BUDGET`` as ``B_do``),
  so only the cost domain reads 100.
* No lodging base is chosen yet (backend#70), so the lodging domain is inactive.
* Health data the caller may not see (stairs sensitivity of someone else) is
  read from the profile instead. The service only generates plans for co-hosts,
  who see it all, so the same data always gives the same plan.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from tuttitrip.places.schemas import CityRead, PlaceRead
from tuttitrip.planning.logic.params import AlgorithmParams
from tuttitrip.planning.schemas import (
    PlanningInput,
    PlanningPerson,
    PlanningTrip,
    Vote,
)
from tuttitrip.profiles.feedback.schemas import RatingValue, ReasonCode, TripFeedback
from tuttitrip.profiles.preferences.schemas import PreferencesRead
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.trips.schemas import TripRead

NO_BUDGET = Decimal(1_000_000_000)
"""``B_do`` of a trip without a budget: effectively unlimited."""
ALGORITHM_VERSION = 1
"""Bumped when the algorithm or its parameters change what a plan means."""

_VOTES: dict[RatingValue, Vote] = {
    RatingValue.WANT: 1,
    RatingValue.NEUTRAL: 0,
    RatingValue.DONT_WANT: -1,
}


class PlanInputError(Exception):
    """The trip lacks something a plan needs (dates or a city)."""


def trip_days(start: date, end: date) -> tuple[date, ...]:
    """Every date of the trip.

    Args:
        start: First day.
        end: Last day.

    Returns:
        The dates from ``start`` to ``end``.
    """
    return tuple(start + timedelta(days=n) for n in range((end - start).days + 1))


def build_input(  # ruff: ignore[too-many-arguments] the data of five domains
    trip: TripRead,
    *,
    city: CityRead,
    profiles: Sequence[ProfileRead],
    preferences: Sequence[PreferencesRead],
    feedback: TripFeedback,
    places: Sequence[PlaceRead],
    must: frozenset[UUID] = frozenset(),
    blocked: frozenset[UUID] = frozenset(),
) -> PlanningInput:
    """Assemble ``PlanningInput`` from the trip, its people and the catalog.

    Args:
        trip: The trip.
        city: The trip's city (time zone and currency).
        profiles: The people on the trip.
        preferences: Their preferences (one per profile).
        feedback: Ratings and active vetoes.
        places: The city's places.
        must: Places the host forces into the plan (E0).
        blocked: Places the host blocked (E0).

    Returns:
        The input of the algorithm.

    Raises:
        PlanInputError: When the trip has no dates or no people.
    """
    if trip.start_date is None or trip.end_date is None:
        msg = "The trip needs start and end dates to plan"
        raise PlanInputError(msg)
    if not profiles:
        msg = "The trip has no people"
        raise PlanInputError(msg)
    by_profile = {p.profile_id: p for p in preferences}
    votes: dict[UUID, dict[UUID, Vote]] = {}
    reasons: dict[UUID, dict[UUID, ReasonCode]] = {}
    for rating in feedback.ratings:
        votes.setdefault(rating.profile_id, {})[rating.place_id] = _VOTES[rating.value]
        if rating.reason_code is not None:
            reasons.setdefault(rating.profile_id, {})[rating.place_id] = (
                rating.reason_code
            )
    vetoes: dict[UUID, set[UUID]] = {}
    for veto in feedback.vetoes:
        vetoes.setdefault(veto.profile_id, set()).add(veto.place_id)
    people = []
    for profile in sorted(profiles, key=lambda p: str(p.id)):
        prefs = by_profile[profile.id]
        stairs = prefs.effective_stairs_sensitivity
        if stairs is None:
            stairs = profile.stairs_sensitivity
        people.append(
            PlanningPerson(
                id=profile.id,
                age=profile.age,
                weight=profile.weight,
                interests=prefs.interests,
                pool=prefs.importance_pool,
                segment_km=profile.segment_km,
                daily_km=profile.daily_km,
                active_min=profile.active_min,
                stairs_sensitivity=stairs,
                queue_patience_min=profile.queue_patience_min,
                nap_start=profile.nap_start,
                nap_minutes=profile.nap_minutes,
                floor=profile.floor,
                votes=votes.get(profile.id, {}),
                vote_reasons=reasons.get(profile.id, {}),
                vetoes=frozenset(vetoes.get(profile.id, set())),
                min_tags=tuple(prefs.min_tags),
            )
        )
    has_budget = trip.budget_total_max is not None
    return PlanningInput(
        trip=PlanningTrip(
            days=trip_days(trip.start_date, trip.end_date),
            timezone=city.timezone,
            day_start=trip.day_start,
            day_end=trip.day_end,
            budget_from=trip.budget_total_min or Decimal(0),
            budget_to=trip.budget_total_max if has_budget else NO_BUDGET,
            flex_pct=trip.budget_flex_pct,
            has_lodging=False,  # ponytail: needs a chosen base, arrives with #70
            currency=trip.currency or city.currency,
        ),
        people=tuple(people),
        places=tuple(sorted(places, key=lambda p: str(p.id))),
        must=must,
        blocked=blocked,
    )


def _canonical(value: object) -> Any:  # ruff: ignore[any-type, too-many-return-statements] flat dispatch on type
    # Dict keys become text, sets become sorted lists, scalars become text, so the
    # JSON is the same in every process whatever the order of the input.
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(v) for v in value), key=str)
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def input_hash(
    data: PlanningInput,
    alpha: float,
    weight_preset: str,
    params: AlgorithmParams,
    solver: str | None = None,
    *,
    parameters_version: int = 0,
) -> str:
    """SHA-256 of everything that decides the plan.

    Args:
        data: The planning input.
        alpha: The fairness slider.
        weight_preset: The weight preset recorded with the plan.
        params: The algorithm parameters.
        solver: Tag of a non-default solver; None keeps the hash of the default.
        parameters_version: Version of the stored parameters (0: built-in).

    Returns:
        64 hex characters; equal for equal input, in every process.
    """
    content = {
        "version": ALGORITHM_VERSION,
        "alpha": alpha,
        "weight_preset": weight_preset,
        "params": asdict(params),
        "parameters_version": parameters_version,
        "input": data.model_dump(mode="python"),
    }
    if solver is not None:
        content["solver"] = solver
    canonical = json.dumps(_canonical(content), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
