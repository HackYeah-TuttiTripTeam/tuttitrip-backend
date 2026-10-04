"""Who edits a check-in and when check-ins expire.

Source story: "ustawić informacje na temat zameldowania, żeby wszyscy inni
wiedzieli jaki jest mój numer pokoju". A room number is personal data, so the
rows do not outlive the trip.
"""

from datetime import date, timedelta

from tuttitrip.trips.schemas import TripRole

NO_END_RETENTION_DAYS = 14
"""Without an end date, check-ins are kept this many days after the start."""


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


def is_over(end_date: date | None, start_date: date | None, today: date) -> bool:
    """Whether the trip has ended, so its check-ins must be gone.

    ``today`` is the UTC date, so the purge may come up to a day before or after
    midnight in the trip's own time zone; that is accepted.

    Args:
        end_date: Last day of the trip, if set.
        start_date: First day of the trip, if set.
        today: Today's date (UTC).

    Returns:
        True from the day after ``end_date``. Without an end date the trip is
        taken to be over ``NO_END_RETENTION_DAYS`` after its start. A trip with
        neither date never ends (its check-ins go with the trip).
    """
    if end_date is None and start_date is not None:
        end_date = start_date + timedelta(days=NO_END_RETENTION_DAYS)
    return end_date is not None and today > end_date
