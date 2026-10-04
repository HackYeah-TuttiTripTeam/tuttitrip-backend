"""Notification endpoints: the caller's own list, unread counter and marking."""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from tuttitrip.notifications.schemas import (
    MarkResult,
    NotificationMark,
    NotificationQuery,
    NotificationRead,
    UnreadCount,
)
from tuttitrip.notifications.services import notification_service, stream_service
from tuttitrip.notifications.services.hub import NotificationHub
from tuttitrip.notifications.services.notification_service import (
    NotificationNotFoundError,
)
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


def get_hub(request: Request) -> NotificationHub:
    """The process-wide hub (created on first use, stopped by the lifespan).

    Args:
        request: The current request.

    Returns:
        The notification hub kept on the application state.
    """
    state = request.app.state
    hub = getattr(state, "notification_hub", None)
    if not isinstance(hub, NotificationHub):
        hub = NotificationHub()
        state.notification_hub = hub
    return hub


HubDep = Annotated[NotificationHub, Depends(get_hub)]


@router.get(
    "/stream",
    response_class=EventSourceResponse,
    dependencies=[requires(Feature.NOTIFICATIONS, Access.READ)],
)
async def stream_notifications(
    user: CurrentUser,
    session: SessionDep,
    hub: HubDep,
    last_event_id: Annotated[
        str | None,
        Header(
            description="Id of the last notification the client received (sent "
            "by EventSource itself; fetch clients set it by hand)."
        ),
    ] = None,
    since: Annotated[
        datetime | None,
        Query(description="Alternative to Last-Event-ID: send what was created since."),
    ] = None,
) -> AsyncIterator[ServerSentEvent]:
    """Live stream (Server-Sent Events) of the caller's new notifications.

    Authorized by the usual `Authorization: Bearer` header, so read it with
    `fetch`, not `EventSource`. Events: `ready` (`{"unread": n}`),
    `notification` (id = notification id, data = a notification) and `resync`
    (reload the list and the counter). A comment `ping` arrives every 15 s. The
    stream ends at the token's expiry and after 30 minutes at the latest;
    reconnect with `Last-Event-ID` (or `since`) to get what was missed.

    Args:
        user: The signed-in user.
        session: Database session (released at once: a stream holds none).
        hub: Source of live notifications.
        last_event_id: Id of the last notification the client has.
        since: Alternative to `last_event_id`.

    Yields:
        The events.
    """
    await session.close()
    async for item in stream_service.events(
        hub, user.sub, token_exp=user.exp, last_event_id=last_event_id, since=since
    ):
        yield ServerSentEvent(
            event=item.event, id=item.id, data=item.data.model_dump(mode="json")
        )


@router.get(
    "/{notification_id}", dependencies=[requires(Feature.NOTIFICATIONS, Access.READ)]
)
async def get_notification(
    notification_id: UUID, user: CurrentUser, session: SessionDep
) -> NotificationRead:
    """Read one of the caller's notifications, however old.

    Args:
        notification_id: The notification.
        user: The signed-in user.
        session: Database session.

    Returns:
        The notification.

    Raises:
        HTTPException: 404 when it does not exist or belongs to someone else.
    """
    try:
        return await notification_service.get_notification(
            session, user.sub, notification_id
        )
    except NotificationNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Notification not found"
        ) from exc
