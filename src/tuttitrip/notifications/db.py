"""Notification queries on PostgreSQL."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.notifications.models import Notification


async def insert_for_recipients(
    session: AsyncSession, rows: Sequence[dict[str, Any]]
) -> int:
    """Insert notifications, skipping those whose ``(user_sub, dedupe_key)`` exists.

    Args:
        session: Open session (the caller commits).
        rows: Column values, one dict per recipient.

    Returns:
        How many rows were actually inserted.
    """
    if not rows:
        return 0
    result = await session.execute(
        insert(Notification)
        .values(list(rows))
        .on_conflict_do_nothing(index_elements=["user_sub", "dedupe_key"])
        .returning(Notification.id)
    )
    return len(result.all())


async def mark_read_by_key(session: AsyncSession, dedupe_key: str) -> int:
    """Mark every unread notification with this key as read.

    Args:
        session: Open session (the caller commits).
        dedupe_key: The key the producer used.

    Returns:
        How many rows changed.
    """
    result = await session.execute(
        update(Notification)
        .where(Notification.dedupe_key == dedupe_key, Notification.read_at.is_(None))
        .values(read_at=func.now())
        .returning(Notification.id)
    )
    return len(result.all())
