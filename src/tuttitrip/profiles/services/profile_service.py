"""Read trip profiles, authorizing through the trips domain's service."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles import db
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.trips.services import trip_service


async def list_profiles(
    session: AsyncSession, trip_id: UUID, owner_sub: str
) -> list[ProfileRead]:
    """List profiles of a trip the caller owns.

    Args:
        session: Open session.
        trip_id: Trip id.
        owner_sub: Auth0 subject of the caller.

    Returns:
        The trip's profiles.
    """
    await trip_service.get_owned_trip(session, trip_id, owner_sub)
    profiles = await db.select_profiles_by_trip(session, trip_id)
    return [ProfileRead.model_validate(profile) for profile in profiles]
