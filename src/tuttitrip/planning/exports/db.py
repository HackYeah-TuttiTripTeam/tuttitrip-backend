"""Queries of the Google exports."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
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


async def upsert_export(
    session: AsyncSession,
    *,
    trip_id: UUID,
    user_sub: str,
    kind: str,
    values: dict[str, object],
) -> None:
    """Create the export row or change it; two parallel saves do not collide.

    Args:
        session: Open session (the caller commits).
        trip_id: Trip id.
        user_sub: Auth0 user id.
        kind: ``calendar`` or ``drive``.
        values: Columns to set (``external_id``, ``event_ids``, ``plan_id``...).
    """
    stmt = insert(GoogleExport).values(
        trip_id=trip_id, user_sub=user_sub, kind=kind, **values
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["trip_id", "user_sub", "kind"], set_=values
        )
    )
