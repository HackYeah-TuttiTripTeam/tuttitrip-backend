"""Notification endpoints: the caller's own list and unread counter."""

from typing import Annotated

from fastapi import APIRouter, Query

from tuttitrip.notifications.schemas import (
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
