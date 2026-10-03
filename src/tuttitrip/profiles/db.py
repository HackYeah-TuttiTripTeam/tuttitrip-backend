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


async def select_profile(
    session: AsyncSession, trip_id: UUID, profile_id: UUID
) -> Profile | None:
    """One profile of a trip.

    Args:
        session: Open session.
        trip_id: Trip id.
        profile_id: Profile id.

    Returns:
        The profile, or None when it is missing or on another trip.
    """
    return await session.scalar(
        select(Profile).where(Profile.id == profile_id, Profile.trip_id == trip_id)
    )


async def select_account_profile_id(
    session: AsyncSession, trip_id: UUID, sub: str
) -> UUID | None:
    """The id of the profile an account has on a trip.

    Args:
        session: Open session.
        trip_id: Trip id.
        sub: Auth0 subject.

    Returns:
        The profile id, or None.
    """
    return await session.scalar(
        select(Profile.id).where(Profile.trip_id == trip_id, Profile.user_sub == sub)
    )


async def insert_profile(session: AsyncSession, profile: Profile) -> Profile:
    """Insert a profile and flush.

    Args:
        session: Open session (caller commits).
        profile: The new profile.

    Returns:
        The same profile.
    """
    session.add(profile)
    await session.flush()
    return profile


async def delete_profile(session: AsyncSession, profile: Profile) -> None:
    """Delete a profile and flush.

    Args:
        session: Open session (caller commits).
        profile: The profile to remove.
    """
    await session.delete(profile)
    await session.flush()
