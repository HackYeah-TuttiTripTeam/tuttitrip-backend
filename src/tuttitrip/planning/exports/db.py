"""Queries of the Google exports."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.exports.models import GoogleExport


async def select_export(
    session: AsyncSession, trip_id: UUID, user_sub: str, kind: str
) -> GoogleExport | None:
    """The export a user made of a trip.

    Args:
        session: Open session.
        trip_id: Trip id.
        user_sub: Auth0 user id of the owner of the Google account.
        kind: ``calendar`` or ``drive``.

    Returns:
        The row, or None when the user never exported.
    """
    return await session.scalar(
        select(GoogleExport).where(
            GoogleExport.trip_id == trip_id,
            GoogleExport.user_sub == user_sub,
            GoogleExport.kind == kind,
        )
    )
