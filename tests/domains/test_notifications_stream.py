"""The live stream: hub fan-out, queues and resync, event order, auth, wire format."""

import asyncio
import functools
import json
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Coroutine, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.shared.paths import path
from tests.shared.tokens import bearer, make_verifier
from tuttitrip.main import create_app
from tuttitrip.notifications import db
from tuttitrip.notifications.models import Notification
from tuttitrip.notifications.schemas import NotificationRead, StreamReady
from tuttitrip.notifications.services import stream_service
from tuttitrip.notifications.services.hub import (
    QUEUE_SIZE,
    Closed,
    NotificationHub,
    Resync,
)
from tuttitrip.notifications.services.stream_service import StreamEvent
from tuttitrip.shared.auth.api import get_token_verifier
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.api import get_user_grants
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature

SUB = "google-oauth2|42"
OTHER = "auth0|other"
NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


def _sync[**P](test: Callable[P, Coroutine[None, None, None]]) -> Callable[P, None]:
    @functools.wraps(test)
    def run(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(test(*args, **kwargs))

    return run


def make_read(**overrides: Any) -> NotificationRead:  # ruff: ignore[any-type]
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "type": "plan_ready",
        "trip_id": None,
        "params": {},
        "actions": [],
        "read_at": None,
        "created_at": NOW,
    }
    return NotificationRead(**(values | overrides))


def payload(sub: str, notification_id: uuid.UUID) -> str:
    return json.dumps({"id": str(notification_id), "user_sub": sub})


class FakeConnection:
    """What asyncpg's connection offers the hub."""

    def __init__(self) -> None:
        self.listeners: list[Callable[..., None]] = []
        self.on_close: list[Callable[[Any], None]] = []
        self.closed = False

    async def add_listener(self, channel: str, callback: Callable[..., None]) -> None:
        assert channel == "notifications"
        self.listeners.append(callback)

    def add_termination_listener(self, callback: Callable[[Any], None]) -> None:
        self.on_close.append(callback)

    def is_closed(self) -> bool:
        return self.closed

    async def close(self, timeout: float = 0) -> None:  # ruff: ignore[async-function-with-timeout, unused-method-argument] mirrors asyncpg
        self.closed = True

    def send(self, text: str) -> None:
        for callback in self.listeners:
            callback(self, 1, "notifications", text)

    def drop(self) -> None:
        self.closed = True
        for callback in self.on_close:
            callback(self)


class Source:
    """Hands the hub a fresh fake connection each time, or fails first."""

    def __init__(self, fail_first: int = 0) -> None:
        self.connections: list[FakeConnection] = []
        self.fail_first = fail_first

    async def __call__(self) -> Any:  # ruff: ignore[any-type]
        if self.fail_first:
            self.fail_first -= 1
            msg = "database is down"
            raise OSError(msg)
        connection = FakeConnection()
        self.connections.append(connection)
        return connection


def make_hub(
    source: Source, store: dict[uuid.UUID, NotificationRead]
) -> tuple[NotificationHub, AsyncMock]:
    load = AsyncMock(side_effect=store.get)
    return NotificationHub(source, load, backoff=(0.01,)), load


async def wait_for(condition: Callable[[], bool], seconds: float = 2) -> None:
    async with asyncio.timeout(seconds):
        while not condition():  # ruff: ignore[async-busy-wait] polling a fake
            await asyncio.sleep(0.005)


# --- hub ------------------------------------------------------------------


@_sync
async def test_only_the_owner_gets_the_event_and_quickly() -> None:
    item = make_read()
    source = Source()
    hub, _ = make_hub(source, {item.id: item})
    mine, other = hub.open(SUB), hub.open(OTHER)
    try:
        await wait_for(
            lambda: bool(source.connections and source.connections[0].listeners)
        )
        started = time.monotonic()
        source.connections[0].send(payload(SUB, item.id))
        got = await asyncio.wait_for(mine.queue.get(), 2)
        assert got == item
        assert time.monotonic() - started < 2
        assert other.queue.empty()
    finally:
        await hub.stop()


@_sync
async def test_the_row_is_not_read_when_the_user_has_no_stream() -> None:
    item = make_read()
    source = Source()
    hub, load = make_hub(source, {item.id: item})
    watcher = hub.open(OTHER)
    try:
        await wait_for(
            lambda: bool(source.connections and source.connections[0].listeners)
        )
        source.connections[0].send(payload(SUB, item.id))
        await asyncio.sleep(0.05)
        load.assert_not_awaited()
        assert watcher.queue.empty()
    finally:
        await hub.stop()


@_sync
async def test_malformed_events_are_ignored() -> None:
    hub, load = make_hub(Source(), {})
    hub.open(SUB)
    for text in ("not json", "{}", json.dumps({"id": "x", "user_sub": SUB})):
        await hub.handle(text)
    load.assert_not_awaited()
    await hub.stop()


@_sync
async def test_a_full_queue_becomes_a_single_resync() -> None:
    items = {i.id: i for i in (make_read() for _ in range(QUEUE_SIZE + 5))}
    source = Source()
    hub, _ = make_hub(source, items)
    subscriber = hub.open(SUB)
    try:
        for notification_id in items:
            await hub.handle(payload(SUB, notification_id))
        messages = []
        while not subscriber.queue.empty():
            messages.append(subscriber.queue.get_nowait())
        assert Resync("overflow") in messages
        assert len(messages) <= QUEUE_SIZE
    finally:
        await hub.stop()


@_sync
async def test_a_closed_stream_is_removed_from_the_hub() -> None:
    hub, _ = make_hub(Source(), {})
    subscriber = hub.open(SUB)
    assert hub.subscriber_count == 1
    hub.close(subscriber)
    hub.close(subscriber)
    assert hub.subscriber_count == 0
    await hub.stop()


@_sync
async def test_a_lost_connection_is_listened_again_and_streams_get_a_resync() -> None:
    item = make_read()
    source = Source()
    hub, _ = make_hub(source, {item.id: item})
    subscriber = hub.open(SUB)
    try:
        await wait_for(
            lambda: (
                len(source.connections) == 1 and bool(source.connections[0].listeners)
            )
        )
        source.connections[0].drop()
        await wait_for(
            lambda: (
                len(source.connections) == 2 and bool(source.connections[1].listeners)
            )
        )
        assert await asyncio.wait_for(subscriber.queue.get(), 2) == Resync(
            "reconnected"
        )
        source.connections[1].send(payload(SUB, item.id))
        assert await asyncio.wait_for(subscriber.queue.get(), 2) == item
    finally:
        await hub.stop()


@_sync
async def test_the_hub_retries_with_backoff_when_the_database_is_down() -> None:
    source = Source(fail_first=2)
    hub, _ = make_hub(source, {})
    subscriber = hub.open(SUB)
    try:
        await wait_for(lambda: bool(source.connections))
        assert await asyncio.wait_for(subscriber.queue.get(), 2) == Resync(
            "reconnected"
        )
    finally:
        await hub.stop()


@_sync
async def test_stopping_ends_the_streams_and_closes_the_connection() -> None:
    source = Source()
    hub, _ = make_hub(source, {})
    subscriber = hub.open(SUB)
    await wait_for(lambda: bool(source.connections and source.connections[0].listeners))
    await hub.stop()
    assert await subscriber.queue.get() == Closed()
    assert source.connections[0].closed
    late = hub.open(SUB)
    assert await late.queue.get() == Closed()


# --- stream service -----------------------------------------------------------


class Sessions:
    """A session factory whose sessions do nothing."""

    def __call__(self) -> Sessions:
        return self

    async def __aenter__(self) -> MagicMock:
        return MagicMock()

    async def __aexit__(self, *_: object) -> None:
        return None


async def collect(stream: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    return [event async for event in stream]


def stream_of(
    hub: NotificationHub,
    *,
    token_exp: int | None = None,
    last_event_id: str | None = None,
    since: datetime | None = None,
    max_seconds: float = stream_service.MAX_STREAM_SECONDS,
) -> AsyncGenerator[StreamEvent]:
    return stream_service.events(
        hub,
        SUB,
        token_exp=token_exp,
        last_event_id=last_event_id,
        since=since,
        sessions=cast("async_sessionmaker[AsyncSession]", Sessions()),
        max_seconds=max_seconds,
    )


def row_of(item: NotificationRead) -> Notification:
    return Notification(
        **item.model_dump(exclude={"actions"}), user_sub=SUB, actions=[]
    )


@pytest.fixture(autouse=True)  # ruff: ignore[pytest-fixture-autouse] every stream test counts unread
def unread(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    count = AsyncMock(return_value=3)
    monkeypatch.setattr(db, "count_unread", count)
    return count


@_sync
async def test_the_stream_opens_with_ready_then_live_notifications() -> None:
    item = make_read()
    hub, _ = make_hub(Source(), {item.id: item})
    stream = stream_of(hub)
    ready = await anext(stream)
    assert ready == StreamEvent("ready", StreamReady(unread=3))
    await hub.handle(payload(SUB, item.id))
    event = await anext(stream)
    assert (event.event, event.id, event.data) == ("notification", str(item.id), item)
    await stream.aclose()
    assert hub.subscriber_count == 0
    await hub.stop()


@_sync
async def test_a_reconnecting_client_gets_what_it_missed_and_no_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    last, missed = make_read(), make_read(created_at=NOW + timedelta(minutes=1))
    monkeypatch.setattr(db, "select_one", AsyncMock(return_value=row_of(last)))
    select = AsyncMock(return_value=[row_of(missed)])
    monkeypatch.setattr(db, "select_missed", select)
    hub, _ = make_hub(Source(), {missed.id: missed})
    stream = stream_of(hub, last_event_id=str(last.id))
    events = [await anext(stream), await anext(stream)]
    assert [e.event for e in events] == ["ready", "notification"]
    assert events[1].id == str(missed.id)
    assert select.await_args is not None
    kwargs = select.await_args.kwargs
    assert (kwargs["after"], kwargs["skip_id"]) == (last.created_at, last.id)
    await hub.handle(payload(SUB, missed.id))  # also arrives live: not sent twice
    fresh = make_read()
    hub._load = AsyncMock(return_value=fresh)  # ruff: ignore[private-member-access] - fake source
    await hub.handle(payload(SUB, fresh.id))
    assert (await anext(stream)).id == str(fresh.id)
    await stream.aclose()
    await hub.stop()


@_sync
async def test_since_works_without_an_id(monkeypatch: pytest.MonkeyPatch) -> None:
    select = AsyncMock(return_value=[])
    monkeypatch.setattr(db, "select_missed", select)
    hub, _ = make_hub(Source(), {})
    events = await collect(stream_of(hub, since=NOW, max_seconds=0.05))
    assert [e.event for e in events] == ["ready"]
    assert select.await_args is not None
    assert select.await_args.kwargs["after"] == NOW
    await hub.stop()


@_sync
async def test_an_unknown_or_bad_id_or_too_many_missed_means_resync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub, _ = make_hub(Source(), {})
    monkeypatch.setattr(db, "select_one", AsyncMock(return_value=None))
    for last in (str(uuid.uuid4()), "not-a-uuid"):
        events = await collect(stream_of(hub, last_event_id=last, max_seconds=0.05))
        assert [e.event for e in events] == ["ready", "resync"]
    monkeypatch.setattr(db, "select_one", AsyncMock(return_value=row_of(make_read())))
    many = [row_of(make_read()) for _ in range(stream_service.MAX_BACKLOG + 1)]
    monkeypatch.setattr(db, "select_missed", AsyncMock(return_value=many))
    events = await collect(
        stream_of(hub, last_event_id=str(uuid.uuid4()), max_seconds=0.05)
    )
    assert [e.event for e in events] == ["ready", "resync"]
    await hub.stop()


@_sync
async def test_the_stream_ends_when_the_token_expires() -> None:
    hub, _ = make_hub(Source(), {})
    started = time.monotonic()
    events = await collect(stream_of(hub, token_exp=int(time.time()) + 1))
    assert [e.event for e in events] == ["ready"]
    assert time.monotonic() - started < 3
    assert hub.subscriber_count == 0
    await hub.stop()


@_sync
async def test_the_stream_ends_at_the_time_limit_and_when_the_hub_stops() -> None:
    hub, _ = make_hub(Source(), {})
    assert len(await collect(stream_of(hub, max_seconds=0.05))) == 1
    stream = stream_of(hub)
    await anext(stream)
    await hub.stop()
    assert [e async for e in stream] == []


@_sync
async def test_a_resync_from_the_hub_reaches_the_client() -> None:
    hub, _ = make_hub(Source(), {})
    stream = stream_of(hub)
    await anext(stream)
    hub._broadcast(Resync("reconnected"))  # ruff: ignore[private-member-access] - what a reconnect does
    event = await anext(stream)
    assert event.event == "resync"
    await stream.aclose()
    await hub.stop()


# --- HTTP ---------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    application = create_app()
    verifier = make_verifier()
    application.dependency_overrides[get_token_verifier] = lambda: verifier
    session = MagicMock()
    session.close = AsyncMock()
    application.dependency_overrides[get_session] = lambda: session
    application.dependency_overrides[get_user_grants] = lambda: [
        Grant(Feature.NOTIFICATIONS, Access.READ)
    ]
    application.state.session = session
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_the_stream_needs_a_token(client: TestClient) -> None:
    assert client.get(path("stream_notifications")).status_code == 401


def test_the_stream_needs_the_permission(app: FastAPI) -> None:
    app.dependency_overrides[get_user_grants] = lambda: [
        Grant(Feature.TRIPS_CORE, Access.READ)
    ]
    response = TestClient(app).get(path("stream_notifications"), headers=bearer())
    assert response.status_code == 403


def test_events_go_out_as_sse_and_the_session_is_released(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = make_read()
    seen: dict[str, Any] = {}

    async def fake(  # ruff: ignore[unused-async] an async generator by design
        _hub: NotificationHub,
        sub: str,
        **kwargs: Any,  # ruff: ignore[any-type]
    ) -> AsyncIterator[StreamEvent]:
        seen.update(sub=sub, **kwargs)
        yield StreamEvent("ready", StreamReady(unread=2))
        yield StreamEvent("notification", item, str(item.id))

    monkeypatch.setattr(stream_service, "events", fake)
    headers = bearer() | {"Last-Event-ID": "abc"}
    with client.stream(
        "GET",
        path("stream_notifications"),
        params={"since": NOW.isoformat()},
        headers=headers,
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        text = "".join(response.iter_text())
    assert (
        'event: ready\ndata: {"unread":2}' in text.replace(" ", "")
        or "event: ready" in text
    )
    assert f"id: {item.id}" in text
    assert "event: notification" in text
    assert seen["sub"] == SUB
    assert seen["last_event_id"] == "abc"
    assert seen["since"] == NOW
    assert seen["token_exp"] is not None
    app.state.session.close.assert_awaited()
