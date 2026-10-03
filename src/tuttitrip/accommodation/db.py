"""Accommodation queries on PostgreSQL."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.accommodation.models import (
    AccommodationRequirement,
    RequirementsVersion,
)


async def select_requirements(
    session: AsyncSession, trip_id: UUID
) -> Sequence[AccommodationRequirement]:
    """Requirements of a trip in a stable order.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        Rows ordered by kind and key.
    """
    result = await session.scalars(
        select(AccommodationRequirement)
        .where(AccommodationRequirement.trip_id == trip_id)
        .order_by(AccommodationRequirement.kind, AccommodationRequirement.key)
    )
    return result.all()


async def select_version(session: AsyncSession, trip_id: UUID) -> int:
    """Change counter of the trip's requirements.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The version, 0 when requirements were never saved.
    """
    version = await session.scalar(
        select(RequirementsVersion.version).where(
            RequirementsVersion.trip_id == trip_id
        )
    )
    return version or 0


async def replace_requirements(
    session: AsyncSession,
    trip_id: UUID,
    rows: Sequence[AccommodationRequirement],
    *,
    bump: bool,
) -> int:
    """Replace all requirements of a trip, bumping the version when asked.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        rows: New requirements (``trip_id`` already set).
        bump: Increment the version (a real change).

    Returns:
        The version after the call.
    """
    if bump:
        await session.execute(
            delete(AccommodationRequirement).where(
                AccommodationRequirement.trip_id == trip_id
            )
        )
        session.add_all(rows)
        upsert = insert(RequirementsVersion).values(trip_id=trip_id, version=1)
        await session.execute(
            upsert.on_conflict_do_update(
                index_elements=[RequirementsVersion.trip_id],
                set_={"version": RequirementsVersion.version + 1},
            )
        )
        await session.flush()
    return await select_version(session, trip_id)
