"""Comfort defaults derived from a person's age.

The numbers are a starting point for tests and demos, not research: the host
corrects them per person only in exceptions (see section 2 of docs/algorytm.md
for what each field means to the solver).
"""

from dataclasses import dataclass
from datetime import time

from tuttitrip.profiles.schemas import AgeGroup

TEEN_FROM = 13
ADULT_FROM = 18
SENIOR_FROM = 65


@dataclass(frozen=True, slots=True)
class ComfortDefaults:
    """Comfort values of one person (the solver inputs ``s``, ``D``, ``A``, ``f``)."""

    segment_km: float
    daily_km: float
    active_min: int
    stairs_sensitivity: float
    queue_patience_min: int
    nap_start: time | None
    nap_minutes: int
    floor: int


FLOOR = 30

# Starting point for tests: tune with real families before relying on it.
DEFAULTS: dict[AgeGroup, ComfortDefaults] = {
    AgeGroup.CHILD: ComfortDefaults(
        segment_km=1.0,
        daily_km=4.0,
        active_min=300,
        stairs_sensitivity=0.6,
        queue_patience_min=15,
        nap_start=time(13, 0),
        nap_minutes=60,
        floor=FLOOR,
    ),
    AgeGroup.TEEN: ComfortDefaults(
        segment_km=2.5,
        daily_km=9.0,
        active_min=540,
        stairs_sensitivity=0.1,
        queue_patience_min=30,
        nap_start=None,
        nap_minutes=0,
        floor=FLOOR,
    ),
    AgeGroup.ADULT: ComfortDefaults(
        segment_km=3.0,
        daily_km=12.0,
        active_min=600,
        stairs_sensitivity=0.2,
        queue_patience_min=40,
        nap_start=None,
        nap_minutes=0,
        floor=FLOOR,
    ),
    AgeGroup.SENIOR: ComfortDefaults(
        segment_km=1.5,
        daily_km=6.0,
        active_min=420,
        stairs_sensitivity=0.7,
        queue_patience_min=20,
        nap_start=time(14, 0),
        nap_minutes=30,
        floor=FLOOR,
    ),
}


def age_group_for(age: int) -> AgeGroup:
    """Age group of a person.

    Args:
        age: Age in years.

    Returns:
        ``child`` below 13, ``teen`` below 18, ``adult`` below 65, else ``senior``.
    """
    if age < TEEN_FROM:
        return AgeGroup.CHILD
    if age < ADULT_FROM:
        return AgeGroup.TEEN
    if age < SENIOR_FROM:
        return AgeGroup.ADULT
    return AgeGroup.SENIOR
