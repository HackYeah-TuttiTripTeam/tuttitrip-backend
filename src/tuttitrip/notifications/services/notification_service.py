"""Producer side of notifications: other domains call these in their transaction.

Neither function commits. The notification then exists if and only if the
caller's change was saved: a rollback takes it back. The service does not check
trip membership; the producer decides who gets the notification. ``params``
reach the stream and the browser, so no tokens, token links or private data.
"""

import logging
import uuid
from collections.abc import Iterable, Sequence

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.notifications import db
from tuttitrip.notifications.models import Notification
from tuttitrip.notifications.schemas import (
    MarkResult,
    NotificationAction,
    NotificationMark,
    NotificationQuery,
    NotificationRead,
    NotificationType,
    UnreadCount,
)
from tuttitrip.shared.pagination.schemas import Page

log = logging.getLogger(__name__)


async def notify(  # ruff: ignore[too-many-arguments] one keyword-only producer call
    session: AsyncSession,
    *,
    recipients: Iterable[str],
    type: NotificationType,  # ruff: ignore[builtin-argument-shadowing] - the column and the API field are `type`
    trip_id: uuid.UUID | None = None,
    params: dict[str, str] | None = None,
    actions: Sequence[NotificationAction] = (),
    dedupe_key: str | None = None,
    actor: str | None = None,
) -> int:
    """Create one notification per recipient, in the caller's transaction.

    Args:
        session: Open session; the caller commits or rolls back.
        recipients: ``sub`` of each recipient; duplicates count once.
        type: What happened.
        trip_id: The trip it concerns, if any.
        params: Small values for the text (names, ids, amounts as strings).
        actions: Buttons, as codes from the closed set.
        dedupe_key: A recipient who already has this key gets nothing new.
        actor: ``sub`` of whoever caused it; they never get their own.

    Returns:
        How many notifications were created.
    """
    subs = dict.fromkeys(sub for sub in recipients if sub != actor)
    return await db.insert_for_recipients(
        session,
        [
            {
                "user_sub": sub,
                "type": type.value,
                "trip_id": trip_id,
                "params": params or {},
                "actions": [a.model_dump(mode="json") for a in actions],
                "dedupe_key": dedupe_key,
            }
            for sub in subs
        ],
    )


async def resolve(session: AsyncSession, dedupe_key: str) -> int:
    """Mark as read every notification with this key, so stale actions disappear.

    Args:
        session: Open session; the caller commits.
        dedupe_key: The key used when the notifications were created.

    Returns:
        How many notifications changed.
    """
    return await db.mark_read_by_key(session, dedupe_key)


def to_read(row: Notification) -> NotificationRead:
    """Read a row without failing on what a newer producer wrote.

    An action code this version does not know is dropped and params that are
    not strings are turned into strings, each with a log line, so one odd row
    never turns the whole list into a 500.

    Args:
        row: The stored notification.

    Returns:
        The notification as the owner sees it.
    """
    actions: list[NotificationAction] = []
    for raw in row.actions:
        try:
            actions.append(NotificationAction.model_validate(raw))
        except ValidationError:
            log.warning("notification %s: unknown action %r skipped", row.id, raw)
    params = {
        str(k): v if isinstance(v, str) else str(v) for k, v in row.params.items()
    }
    if params != row.params:
        log.warning("notification %s: non-string params coerced", row.id)
    return NotificationRead(
        id=row.id,
        type=row.type,
        trip_id=row.trip_id,
        params=params,
        actions=actions,
        read_at=row.read_at,
        created_at=row.created_at,
    )


async def list_notifications(
    session: AsyncSession, caller: str, query: NotificationQuery
) -> Page[NotificationRead]:
    """One page of the caller's notifications, filtered and sorted.

    Args:
        session: Open session.
        caller: ``sub`` of the signed-in user; others' rows never appear.
        query: Paging, sorting and filters.

    Returns:
        The page of notifications.
    """
    page = await db.select_page(session, caller, query)
    return Page[NotificationRead](
        items=[to_read(n) for n in page.items],
        total=page.total,
        page=page.page,
        size=page.size,
        pages=page.pages,
    )


async def unread_count(session: AsyncSession, caller: str) -> UnreadCount:
    """How many notifications the caller has not read.

    Args:
        session: Open session.
        caller: ``sub`` of the signed-in user.

    Returns:
        The count.
    """
    return UnreadCount(count=await db.count_unread(session, caller))


async def mark(
    session: AsyncSession, caller: str, selection: NotificationMark
) -> MarkResult:
    """Mark the caller's notifications read or unread, by ids or by filter.

    Idempotent: only rows that change state count. A filter selection means the
    rows matching at the time of the call.

    Args:
        session: Open session.
        caller: ``sub`` of the signed-in user; others' rows are never touched.
        selection: Which notifications and which state.

    Returns:
        How many notifications changed.
    """
    updated = await db.set_read(session, caller, selection)
    await session.commit()
    return MarkResult(updated=updated)


class NotificationNotFoundError(Exception):
    """No such notification for this user (unknown or someone else's)."""


async def get_notification(
    session: AsyncSession, caller: str, notification_id: uuid.UUID
) -> NotificationRead:
    """Read one of the caller's notifications (for ``?open=<id>`` deep links).

    Args:
        session: Open session.
        caller: ``sub`` of the signed-in user.
        notification_id: The notification.

    Returns:
        The notification, read as tolerantly as the list.

    Raises:
        NotificationNotFoundError: Unknown id, or someone else's (not told apart).
    """
    row = await db.select_one(session, caller, notification_id)
    if row is None:
        raise NotificationNotFoundError(str(notification_id))
    return to_read(row)


async def erase_account(session: AsyncSession, sub: str) -> dict[str, int]:
    """Delete the notifications of a deleted account, without committing.

    Args:
        session: Open session (caller commits).
        sub: Auth0 subject of the deleted account.

    Returns:
        ``notifications_deleted``.
    """
    return {"notifications_deleted": await db.delete_for_user(session, sub)}
