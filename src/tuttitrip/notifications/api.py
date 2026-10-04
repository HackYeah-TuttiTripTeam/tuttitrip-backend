"""Notification endpoints: the caller's own list, unread counter and marking."""

from typing import Annotated

from fastapi import APIRouter, Query

from tuttitrip.notifications.schemas import (
    MarkResult,
    NotificationMark,
    NotificationQuery,
    NotificationRead,
    UnreadCount,
)
from tuttitrip.notifications.services import notification_service
from tuttitrip.shared.auth.api import CurrentUser
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", dependencies=[requires(Feature.NOTIFICATIONS, Access.READ)])
async def list_notifications(
    user: CurrentUser,
    session: SessionDep,
    query: Annotated[NotificationQuery, Query()],
) -> Page[NotificationRead]:
    """List the caller's notifications, newest first by default.

    Only the caller's own notifications exist for this endpoint; there is no
    403 for someone else's. `type` can be repeated (`?type=a&type=b`).

    Args:
        user: The signed-in user.
        session: Database session.
        query: Paging, sorting (`created_at` or `type`) and filters.

    Returns:
        One page with the total of matching notifications.
    """
    return await notification_service.list_notifications(session, user.sub, query)


@router.get(
    "/unread-count", dependencies=[requires(Feature.NOTIFICATIONS, Access.READ)]
)
async def unread_count(user: CurrentUser, session: SessionDep) -> UnreadCount:
    """Count the caller's unread notifications (for the badge).

    Args:
        user: The signed-in user.
        session: Database session.

    Returns:
        The number of unread notifications.
    """
    return await notification_service.unread_count(session, user.sub)


@router.post("/mark", dependencies=[requires(Feature.NOTIFICATIONS, Access.WRITE)])
async def mark_notifications(
    body: NotificationMark, user: CurrentUser, session: SessionDep
) -> MarkResult:
    """Mark notifications read or unread, selected by ids or by filters.

    One `UPDATE`, idempotent. Ids that are not the caller's are skipped without
    a trace (no difference between missing and foreign). A filter selection
    covers everything matching now, including notifications that arrived after
    the list was last refreshed.

    Args:
        body: `read` plus exactly one of `ids` (max 100) and `filters`.
        user: The signed-in user.
        session: Database session.

    Returns:
        How many notifications actually changed state.
    """
    return await notification_service.mark(session, user.sub, body)
