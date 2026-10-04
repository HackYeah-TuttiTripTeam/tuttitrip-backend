"""Notification queries on PostgreSQL."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.notifications.models import Notification
from tuttitrip.notifications.schemas import (
    NotificationFilter,
    NotificationQuery,
    NotificationSort,
)
from tuttitrip.shared.db.pagination import ordering, paginate
from tuttitrip.shared.pagination.schemas import Page

COLUMNS = {
    NotificationSort.CREATED_AT: Notification.created_at,
    NotificationSort.TYPE: Notification.type,
}


def scoped(caller: str) -> Select[Notification]:
    """Every query starts here: the caller's own notifications.

    Args:
        caller: ``sub`` of the signed-in user.

    Returns:
        A select of ``Notification``; other users' rows never appear.
    """
    return select(Notification).where(Notification.user_sub == caller)


def apply_filters(
    stmt: Select[Notification], filters: NotificationFilter
) -> Select[Notification]:
    """Add the filters to a select.

    Args:
        stmt: A select of ``Notification`` (already scoped to the caller).
        filters: The requested filters; unset ones add nothing.

    Returns:
        The narrowed select.
    """
    if filters.read is not None:
        stmt = stmt.where(
            Notification.read_at.is_not(None)
            if filters.read
            else Notification.read_at.is_(None)
        )
    if filters.type:
        stmt = stmt.where(Notification.type.in_(filters.type))
    if filters.trip_id is not None:
        stmt = stmt.where(Notification.trip_id == filters.trip_id)
    if filters.created_from is not None:
        stmt = stmt.where(Notification.created_at >= filters.created_from)
    if filters.created_to is not None:
        stmt = stmt.where(Notification.created_at < filters.created_to)
    return stmt


async def select_page(
    session: AsyncSession, caller: str, query: NotificationQuery
) -> Page[Notification]:
    """One page of the caller's notifications.

    Sorting by type breaks ties by ``created_at`` and then ``id``, all in the
    requested direction, so pages are stable.

    Args:
        session: Open session.
        caller: ``sub`` of the signed-in user.
        query: Paging, sorting and filters.

    Returns:
        The page with the total of matching rows.
    """
    order = ordering(COLUMNS, query.sort, Notification.id)
    if query.sort is NotificationSort.TYPE:
        order = (order[0], Notification.created_at, order[-1])
    return await paginate(session, apply_filters(scoped(caller), query), query, order)


async def count_unread(session: AsyncSession, caller: str) -> int:
    """How many unread notifications the caller has (one indexed count).

    Args:
        session: Open session.
        caller: ``sub`` of the signed-in user.

    Returns:
        The number of rows with ``read_at IS NULL``.
    """
    return (
        await session.scalar(
            select(func.count()).where(
                Notification.user_sub == caller, Notification.read_at.is_(None)
            )
        )
    ) or 0


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


async def delete_for_user(session: AsyncSession, sub: str) -> int:
    """Delete every notification of one account.

    Args:
        session: Open session (the caller commits).
        sub: Auth0 subject of the deleted account.

    Returns:
        How many rows were deleted.
    """
    result = await session.execute(
        delete(Notification)
        .where(Notification.user_sub == sub)
        .returning(Notification.id)
    )
    return len(result.all())
