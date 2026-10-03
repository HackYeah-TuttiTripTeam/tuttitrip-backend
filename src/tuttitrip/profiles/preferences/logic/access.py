"""The stairs sensitivity the solver uses, computed from the constraints."""

from tuttitrip.profiles.preferences.schemas import Constraints

BLOCKED = 1.0


def effective_stairs_sensitivity(
    constraints: Constraints, profile_sensitivity: float
) -> float:
    """Stairs sensitivity after the access constraints.

    Stairs or a wheelchair make every place with stairs unusable (the spec
    rejects a place at ``stairs * sensitivity >= 0.9``). Nothing is written to
    the profile, so a later age change or a hand-tuned value still applies once
    the constraint is cleared. The solver (#45) reads ``wheelchair`` directly as
    a hard exclusion of any place with ``stairs > 0``.

    Args:
        constraints: The person's access constraints.
        profile_sensitivity: ``stairs_sensitivity`` of the profile.

    Returns:
        1.0 when stairs or wheelchair is set, else the profile's value.
    """
    if constraints.stairs or constraints.wheelchair:
        return BLOCKED
    return profile_sensitivity
