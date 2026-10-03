"""How the access constraints move the stairs sensitivity of a profile."""

BLOCKED = 1.0


def stairs_sensitivity(*, blocked: bool, current: float, age_default: float) -> float:
    """Stairs sensitivity after the "stairs" or "wheelchair" constraint changed.

    A set constraint makes every place with stairs unusable (the spec rejects a
    place at ``stairs * sensitivity >= 0.9``). Clearing it gives back the age
    default, but only when the value is still the one the constraint set, so a
    sensitivity the host tuned by hand survives.

    Args:
        blocked: Whether stairs or wheelchair is set.
        current: The profile's sensitivity now.
        age_default: The default of the profile's age group.

    Returns:
        The sensitivity to store.
    """
    if blocked:
        return BLOCKED
    return age_default if current == BLOCKED else current
