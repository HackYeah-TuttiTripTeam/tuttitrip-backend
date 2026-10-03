"""Profile queries on PostgreSQL."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles.models import Profile


async def select_profiles_by_trip(
    session: AsyncSession, trip_id: UUID
) -> Sequence[Profile]:
    """List the profiles of one trip.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        Profiles ordered by name.
    """
    result = await session.scalars(
        select(Profile).where(Profile.trip_id == trip_id).order_by(Profile.display_name)
    )
    return result.all()
