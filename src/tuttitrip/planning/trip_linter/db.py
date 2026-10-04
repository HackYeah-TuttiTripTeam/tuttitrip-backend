"""Paste check queries on PostgreSQL."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.trip_linter.models import PasteCheck


async def insert_check(session: AsyncSession, check: PasteCheck) -> PasteCheck:
    """Insert a check and flush to get server defaults.

    Args:
        session: Open session (caller commits).
        check: The new row.

    Returns:
        The persisted row.
    """
    session.add(check)
    await session.flush()
    await session.refresh(check)
    return check


async def select_check(
    session: AsyncSession, trip_id: UUID, paste_id: UUID
) -> PasteCheck | None:
    """A check of a trip.

    Args:
        session: Open session.
        trip_id: Trip id (a check of another trip is not found).
        paste_id: The pasted document's id.

    Returns:
        The row, or None.
    """
    return await session.scalar(
        select(PasteCheck).where(
            PasteCheck.trip_id == trip_id, PasteCheck.id == paste_id
        )
    )
