"""The live stream of one user: what is sent, in which order, and when it ends.

Events: ``ready`` (with the unread counter), ``notification`` (SSE ``id`` is the
notification id, so a reconnecting client sends it back in ``Last-Event-ID``)
and ``resync`` (reload everything). A client that reconnects with
``Last-Event-ID`` (the id of the last notification it saw) or ``since`` (a
moment) first gets what it missed; more than ``MAX_BACKLOG`` missed, or an
unknown id, means ``resync``. The stream holds no database session: it reads in
short ones. It ends at the token's expiry and after ``MAX_STREAM_SECONDS``.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tuttitrip.notifications import db
from tuttitrip.notifications.schemas import (
    NotificationRead,
    StreamReady,
    StreamResync,
)
from tuttitrip.notifications.services import notification_service
from tuttitrip.notifications.services.hub import Closed, NotificationHub, Resync
from tuttitrip.shared.db.session import get_sessionmaker

MAX_STREAM_SECONDS = 30 * 60
MAX_BACKLOG = 50


@dataclass(frozen=True)
class StreamEvent:
    """One event to put on the wire."""

    event: str
    data: StreamReady | NotificationRead | StreamResync
    id: str | None = None


def _as_event(item: NotificationRead | Resync) -> StreamEvent:
    if isinstance(item, Resync):
        return StreamEvent("resync", StreamResync(reason=item.reason))
    return StreamEvent("notification", item, str(item.id))


async def _missed(
    sessions: async_sessionmaker[AsyncSession],
    sub: str,
    last_event_id: str | None,
    since: datetime | None,
) -> list[NotificationRead] | Resync:
    """What the client missed while disconnected.

    Args:
        sessions: Session factory.
        sub: Auth0 subject of the stream's owner.
        last_event_id: Id of the last notification the client has.
        since: Alternative to ``last_event_id``.

    Returns:
        The missed notifications oldest first, or ``Resync`` when that is too
        many or the id is unknown.
    """
    if last_event_id is None and since is None:
        return []
    skip_id: uuid.UUID | None = None
    after = since
    async with sessions() as session:
        if last_event_id is not None:
            try:
                skip_id = uuid.UUID(last_event_id)
            except ValueError:
                return Resync("bad_last_event_id")
            seen = await db.select_one(session, sub, skip_id)
            if seen is None:
                return Resync("unknown_last_event_id")
            after = seen.created_at
        if after is None:  # unreachable: one of the two is set
            return []
        rows = await db.select_missed(
            session, sub, after=after, skip_id=skip_id, limit=MAX_BACKLOG + 1
        )
    if len(rows) > MAX_BACKLOG:
        return Resync("too_many_missed")
    return [notification_service.to_read(row) for row in rows]


async def events(  # ruff: ignore[too-many-arguments] keyword-only stream parameters
    hub: NotificationHub,
    sub: str,
    *,
    token_exp: int | None,
    last_event_id: str | None = None,
    since: datetime | None = None,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    max_seconds: float = MAX_STREAM_SECONDS,
) -> AsyncGenerator[StreamEvent]:
    """Events of one stream until the token expires, the time is up or the hub stops.

    Args:
        hub: Source of live notifications.
        sub: Auth0 subject of the stream's owner.
        token_exp: Token expiry in Unix seconds (None: only the time limit applies).
        last_event_id: Id of the last notification the client has, if reconnecting.
        since: Alternative to ``last_event_id``: send what was created since then.
        sessions: Session factory (tests); defaults to the application's.
        max_seconds: Longest the stream stays open.

    Yields:
        The events, ``ready`` first.
    """
    sessions = sessions or get_sessionmaker()
    deadline = time.monotonic() + max_seconds
    if token_exp is not None:
        deadline = min(deadline, time.monotonic() + (token_exp - time.time()))
    subscriber = hub.open(sub)
    try:
        # Subscribed before the reads: nothing falls between backlog and live events.
        async with sessions() as session:
            unread = await db.count_unread(session, sub)
        missed = await _missed(sessions, sub, last_event_id, since)
        yield StreamEvent("ready", StreamReady(unread=unread))
        sent: set[uuid.UUID] = set()
        for item in [missed] if isinstance(missed, Resync) else missed:
            if isinstance(item, NotificationRead):
                sent.add(item.id)
            yield _as_event(item)
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                message = await asyncio.wait_for(subscriber.queue.get(), remaining)
            except TimeoutError:
                return
            if isinstance(message, Closed):
                return
            if isinstance(message, NotificationRead) and message.id in sent:
                continue
            yield _as_event(message)
    finally:
        hub.close(subscriber)
