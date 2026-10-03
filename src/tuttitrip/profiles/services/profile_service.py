"""Read trip profiles."""

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles import db
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.trips.schemas import TripMembership


async def list_profiles(
    session: AsyncSession, membership: TripMembership
) -> list[ProfileRead]:
    """List profiles of a trip.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.

    Returns:
        The trip's profiles.
    """
    profiles = await db.select_profiles_by_trip(session, membership.trip_id)
    return [ProfileRead.model_validate(profile) for profile in profiles]
