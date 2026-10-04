"""One ``LISTEN`` connection per process, fanned out to the open streams.

The trigger from the notifications migration sends ``{id, user_sub}`` on the
``notifications`` channel after each commit. The hub keeps one direct asyncpg
connection (not from the pool, not through PgBouncer in transaction mode) and
hands each notification to the streams of its owner. Rows are read only for
users that have a stream open.

A subscriber has a bounded queue. When it is full, or when the hub lost its
database connection and came back, the subscriber gets a single ``Resync``
instead of the events it may have missed: the client then reloads its list.
The hub starts on the first subscription and is stopped by the app's lifespan.
"""

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tuttitrip.notifications import db
from tuttitrip.notifications.schemas import NotificationRead
from tuttitrip.notifications.services import notification_service
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import get_sessionmaker

CHANNEL = "notifications"
QUEUE_SIZE = 100
BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resync:
    """Events may have been missed: the client reloads its list."""

    reason: str


@dataclass(frozen=True)
class Closed:
    """The hub is shutting down: the stream ends."""


type Message = NotificationRead | Resync | Closed
type Connect = Callable[[], Awaitable[asyncpg.Connection]]
type Load = Callable[[uuid.UUID], Awaitable[NotificationRead | None]]


@dataclass(eq=False)
class Subscriber:
    """One open stream of one user."""

    sub: str
    queue: asyncio.Queue[Message] = field(
        default_factory=lambda: asyncio.Queue(maxsize=QUEUE_SIZE)
    )

    def put(self, message: Message) -> None:
        """Queue a message; a full queue is replaced by one ``Resync``.

        Args:
            message: What to deliver.
        """
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.replace_with(Resync("overflow"))

    def replace_with(self, message: Message) -> None:
        """Drop everything queued and queue ``message`` alone.

        Args:
            message: The only message left.
        """
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(message)


async def connect_direct() -> asyncpg.Connection:
    """Open the dedicated listener connection from the database settings.

    Returns:
        A new asyncpg connection.
    """
    db_settings = get_settings().database
    return cast(
        "asyncpg.Connection",
        await asyncpg.connect(
            user=db_settings.user,
            password=db_settings.password.get_secret_value(),
            host=db_settings.host,
            port=db_settings.port,
            database=db_settings.name,
        ),
    )


async def load_notification(
    sessions: async_sessionmaker[AsyncSession], notification_id: uuid.UUID
) -> NotificationRead | None:
    """Read a notification in a short session.

    Args:
        sessions: Session factory.
        notification_id: The id from the ``NOTIFY`` payload.

    Returns:
        The notification, or None when it is gone.
    """
    async with sessions() as session:
        row = await db.select_by_id(session, notification_id)
    return None if row is None else notification_service.to_read(row)


async def load_default(notification_id: uuid.UUID) -> NotificationRead | None:
    """Read a notification with the application's session factory.

    Args:
        notification_id: The id from the ``NOTIFY`` payload.

    Returns:
        The notification, or None when it is gone.
    """
    return await load_notification(get_sessionmaker(), notification_id)


class NotificationHub:
    """Fans ``NOTIFY`` events out to the streams of their owners."""

    def __init__(
        self,
        connect: Connect = connect_direct,
        load: Load | None = None,
        backoff: tuple[float, ...] = BACKOFF_SECONDS,
    ) -> None:
        """Create a hub; nothing connects until the first subscription.

        Args:
            connect: Opens the listener connection (replaced in tests).
            load: Reads a notification by id; defaults to a short session.
            backoff: Seconds to wait between reconnect attempts (the last repeats).
        """
        self._connect = connect
        self._load = load or load_default
        self._backoff = backoff
        self._subscribers: dict[str, set[Subscriber]] = {}
        self._inbox: asyncio.Queue[str] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self._connection: asyncpg.Connection | None = None
        self._stopping = False

    @property
    def subscriber_count(self) -> int:
        """Open streams right now.

        Returns:
            The number of subscribers over all users.
        """
        return sum(len(group) for group in self._subscribers.values())

    def open(self, sub: str) -> Subscriber:
        """Register a stream of ``sub``; the caller must ``close`` it.

        Args:
            sub: Auth0 subject of the stream's owner.

        Returns:
            The subscriber to read messages from.
        """
        self._ensure_started()
        subscriber = Subscriber(sub)
        if self._stopping:
            subscriber.put(Closed())
        self._subscribers.setdefault(sub, set()).add(subscriber)
        return subscriber

    def close(self, subscriber: Subscriber) -> None:
        """Remove a stream (idempotent).

        Args:
            subscriber: What ``open`` returned.
        """
        group = self._subscribers.get(subscriber.sub)
        if group is not None:
            group.discard(subscriber)
            if not group:
                del self._subscribers[subscriber.sub]

    def _ensure_started(self) -> None:
        if not self._tasks and not self._stopping:
            self._tasks = [
                asyncio.create_task(self._listen_forever()),
                asyncio.create_task(self._dispatch_forever()),
            ]

    async def stop(self) -> None:
        """End every stream and close the listener connection."""
        self._stopping = True
        for group in self._subscribers.values():
            for subscriber in group:
                subscriber.replace_with(Closed())
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        await self._close_connection()

    async def _close_connection(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None and not connection.is_closed():
            with contextlib.suppress(Exception):
                await connection.close(timeout=2)

    def on_notify(self, payload: str) -> None:
        """Take a ``NOTIFY`` payload in; it is handled in arrival order.

        Args:
            payload: JSON ``{"id": ..., "user_sub": ...}`` from the trigger.
        """
        self._inbox.put_nowait(payload)

    async def _dispatch_forever(self) -> None:
        while True:
            await self.handle(await self._inbox.get())

    async def handle(self, payload: str) -> None:
        """Deliver one notification to its owner's streams, if there are any.

        Args:
            payload: JSON ``{"id": ..., "user_sub": ...}`` from the trigger.
        """
        try:
            data = json.loads(payload)
            sub = str(data["user_sub"])
            notification_id = uuid.UUID(str(data["id"]))
        except ValueError, KeyError, TypeError:
            log.warning("ignored a malformed notification event")
            return
        if sub not in self._subscribers:
            return
        try:
            notification = await self._load(notification_id)
        except Exception:
            log.exception("could not read notification %s", notification_id)
            self._broadcast(Resync("load_failed"))
            return
        if notification is None:
            return
        for subscriber in tuple(self._subscribers.get(sub, ())):
            subscriber.put(notification)

    def _broadcast(self, message: Message) -> None:
        for group in self._subscribers.values():
            for subscriber in group:
                subscriber.put(message)

    async def _listen_forever(self) -> None:
        """Keep a ``LISTEN`` connection; after a loss, reconnect and ``Resync``."""
        attempt = 0
        ever_connected = False
        while True:
            lost = asyncio.Event()
            try:
                connection = await self._connect()
                connection.add_termination_listener(lambda _c, event=lost: event.set())
                await connection.add_listener(
                    CHANNEL, lambda _c, _pid, _ch, payload: self.on_notify(payload)
                )
            except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
                delay = self._backoff[min(attempt, len(self._backoff) - 1)]
                attempt += 1
                log.warning(
                    "notification hub cannot listen (%s); retry in %ss", exc, delay
                )
                await asyncio.sleep(delay)
                continue
            self._connection = connection
            if ever_connected or attempt:
                self._broadcast(Resync("reconnected"))
            attempt = 0
            ever_connected = True
            await lost.wait()
            log.warning("notification hub lost its database connection")
            await self._close_connection()
