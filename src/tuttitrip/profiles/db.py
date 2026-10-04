"""Profile queries on PostgreSQL."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select, update
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


async def select_claimable(session: AsyncSession, trip_id: UUID) -> Sequence[Profile]:
    """Profiles of one trip that have no account.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        Profiles without ``user_sub`` ordered by name.
    """
    result = await session.scalars(
        select(Profile)
        .where(Profile.trip_id == trip_id, Profile.user_sub.is_(None))
        .order_by(Profile.display_name)
    )
    return result.all()


async def link_account(
    session: AsyncSession, trip_id: UUID, profile_id: UUID, sub: str
) -> bool:
    """Link an account to a profile that has none (safe under concurrent claims).

    One conditional ``UPDATE``: ``user_sub IS NULL`` in the ``WHERE`` makes the
    second of two racing claims match no row.

    Args:
        session: Open session (caller commits).
        trip_id: Trip the profile must belong to.
        profile_id: Profile id.
        sub: Auth0 subject to link.

    Returns:
        False when the profile is not on this trip or already has an account.
    """
    result = await session.execute(
        update(Profile)
        .where(
            Profile.id == profile_id,
            Profile.trip_id == trip_id,
            Profile.user_sub.is_(None),
        )
        .values(user_sub=sub)
        .returning(Profile.id)
    )
    return result.scalar_one_or_none() is not None


async def detach_account_everywhere(session: AsyncSession, sub: str) -> None:
    """Clear the account link of all the user's profiles (caller commits).

    Args:
        session: Open session.
        sub: Auth0 subject.
    """
    await session.execute(
        update(Profile).where(Profile.user_sub == sub).values(user_sub=None)
    )


async def select_profile_ids_of_account(
    session: AsyncSession, sub: str
) -> Sequence[UUID]:
    """Ids of all the profiles linked to an account, on every trip.

    Args:
        session: Open session.
        sub: Auth0 subject.

    Returns:
        The profile ids.
    """
    return (
        await session.scalars(select(Profile.id).where(Profile.user_sub == sub))
    ).all()
