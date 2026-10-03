"""Key dictionary of accommodation requirements, shared with tuttitrip-worker#25.

Amenity keys are the ``places`` catalog's ``Amenity`` values (one vocabulary
for places and for requirements); platforms and the distance key live here.
"""

from enum import StrEnum, unique

from tuttitrip.places.schemas import Amenity


@unique
class RequirementKind(StrEnum):
    """What a requirement is about."""

    AMENITY = "amenity"
    PLATFORM = "platform"
    DISTANCE = "distance"


@unique
class Platform(StrEnum):
    """Booking platform, checked deterministically by the offer link's domain."""

    AIRBNB = "airbnb"
    BOOKING = "booking"


@unique
class DistanceKey(StrEnum):
    """What the maximum distance is measured to."""

    ATTRACTIONS = "attractions"


KEYS: dict[RequirementKind, type[StrEnum]] = {
    RequirementKind.AMENITY: Amenity,
    RequirementKind.PLATFORM: Platform,
    RequirementKind.DISTANCE: DistanceKey,
}


def is_known_key(kind: RequirementKind, key: str) -> bool:
    """Whether ``key`` belongs to the dictionary of ``kind``.

    Args:
        kind: Requirement kind.
        key: Candidate key.

    Returns:
        True when the key is a value of the kind's enum.
    """
    return key in {member.value for member in KEYS[kind]}
