"""Preference queries on PostgreSQL."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles.models import Profile
from tuttitrip.profiles.preferences.models import ProfilePreferences


async def select_preferences(
    session: AsyncSession, profile_id: UUID
) -> ProfilePreferences | None:
    """Stored preferences of one person.

    Args:
        session: Open session.
        profile_id: Profile id.

    Returns:
        The row, or None when nobody has saved preferences yet.
    """
    return await session.get(ProfilePreferences, profile_id)


async def select_preferences_by_trip(
    session: AsyncSession, trip_id: UUID
) -> Sequence[ProfilePreferences]:
    """Stored preferences of every person on a trip.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The saved rows (people without preferences have none).
    """
    result = await session.scalars(
        select(ProfilePreferences)
        .join(Profile, Profile.id == ProfilePreferences.profile_id)
        .where(Profile.trip_id == trip_id)
    )
    return result.all()


async def insert_preferences(
    session: AsyncSession, preferences: ProfilePreferences
) -> ProfilePreferences:
    """Insert a row and flush.

    Args:
        session: Open session (caller commits).
        preferences: The new row.

    Returns:
        The same row.
    """
    session.add(preferences)
    await session.flush()
    return preferences
