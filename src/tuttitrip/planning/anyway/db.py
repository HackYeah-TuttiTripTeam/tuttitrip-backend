"""Queries of the "anyway" suggestion state."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.anyway.models import AnywayState


async def select_rejected(
    session: AsyncSession, trip_id: UUID
) -> set[tuple[int, UUID]]:
    """The (day, place) pairs the host rejected, in any version of the plan.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The pairs.
    """
    rows = await session.execute(
        select(AnywayState.day, AnywayState.place_id).where(
            AnywayState.trip_id == trip_id, AnywayState.rejected_at.is_not(None)
        )
    )
    return {(day, place_id) for day, place_id in rows}


async def select_justifications(
    session: AsyncSession, plan_id: UUID
) -> dict[tuple[int, UUID], str]:
    """The model's texts stored for one plan version.

    Args:
        session: Open session.
        plan_id: Plan version id.

    Returns:
        The text by (day, place).
    """
    rows = await session.execute(
        select(AnywayState.day, AnywayState.place_id, AnywayState.justification).where(
            AnywayState.plan_id == plan_id, AnywayState.justification.is_not(None)
        )
    )
    return {(day, place_id): text for day, place_id, text in rows if text}


async def upsert_state(  # ruff: ignore[too-many-arguments] the columns of one row
    session: AsyncSession,
    *,
    trip_id: UUID,
    plan_id: UUID,
    day: int,
    place_id: UUID,
    changes: dict[str, object],
) -> None:
    """Create the state row of a suggestion or change it.

    Args:
        session: Open session (the caller commits).
        trip_id: Trip id.
        plan_id: Plan version id.
        day: 1-based day.
        place_id: The suggested place.
        changes: Columns to set (``rejected_at``, ``justification``...).
    """
    stmt = insert(AnywayState).values(
        trip_id=trip_id, plan_id=plan_id, day=day, place_id=place_id, **changes
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["plan_id", "day", "place_id"], set_=changes
        )
    )
