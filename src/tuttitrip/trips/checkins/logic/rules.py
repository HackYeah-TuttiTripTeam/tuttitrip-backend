"""Who edits a check-in and when check-ins expire.

Source story: "ustawić informacje na temat zameldowania, żeby wszyscy inni
wiedzieli jaki jest mój numer pokoju". A room number is personal data, so the
rows do not outlive the trip.
"""

from datetime import date

from tuttitrip.trips.schemas import TripRole


def can_edit(role: TripRole, caller_sub: str, owner_sub: str | None) -> bool:
    """Whether the caller may set or clear the check-in of a profile.

    A member changes only their own entry. The host also changes entries of
    profiles without an account (nobody else could fill those in).

    Args:
        role: The caller's role on the trip.
        caller_sub: Auth0 subject of the caller.
        owner_sub: Auth0 subject linked to the profile, if any.

    Returns:
        True when the change is allowed.
    """
    if owner_sub is None:
        return role is TripRole.HOST
    return owner_sub == caller_sub


def is_over(end_date: date | None, today: date) -> bool:
    """Whether the trip has ended, so its check-ins must be gone.

    Args:
        end_date: Last day of the trip, if set.
        today: Today's date.

    Returns:
        True from the day after ``end_date``. A trip without an end date never ends.
    """
    return end_date is not None and today > end_date
