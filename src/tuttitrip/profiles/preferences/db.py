"""Preference queries on PostgreSQL."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
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


async def upsert_preferences(
    session: AsyncSession, profile_id: UUID, values: dict[str, Any], sub: str
) -> ProfilePreferences:
    """Insert the preferences of a person, or replace the stored ones.

    One statement, so two first writes cannot collide on the primary key.

    Args:
        session: Open session (caller commits).
        profile_id: Whose preferences.
        values: The JSON columns (interests, importance_pool, constraints,
            diet, example_places, min_tags).
        sub: Author of this write.

    Returns:
        The stored row.
    """
    stmt = insert(ProfilePreferences).values(
        profile_id=profile_id, updated_by_sub=sub, **values
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["profile_id"],
        set_={
            **{key: stmt.excluded[key] for key in values},
            "updated_by_sub": stmt.excluded.updated_by_sub,
            "updated_at": func.now(),
        },
    ).returning(ProfilePreferences)
    return (
        await session.scalars(stmt, execution_options={"populate_existing": True})
    ).one()
