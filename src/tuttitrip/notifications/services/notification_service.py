"""Producer side of notifications: other domains call these in their transaction.

Neither function commits. The notification then exists if and only if the
caller's change was saved: a rollback takes it back. The service does not check
trip membership; the producer decides who gets the notification. ``params``
reach the stream and the browser, so no tokens, token links or private data.
"""

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.notifications import db
from tuttitrip.notifications.schemas import NotificationAction, NotificationType


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


async def erase_account(session: AsyncSession, sub: str) -> dict[str, int]:
    """Delete the notifications of a deleted account, without committing.

    Args:
        session: Open session (caller commits).
        sub: Auth0 subject of the deleted account.

    Returns:
        ``notifications_deleted``.
    """
    return {"notifications_deleted": await db.delete_for_user(session, sub)}
