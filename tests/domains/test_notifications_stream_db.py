"""The stream on a real PostgreSQL: LISTEN/NOTIFY through the hub, reconnect, backlog.

Needs a migrated database (`uv run alembic upgrade head`, see AGENTS.md).
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import cast

import asyncpg
import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tuttitrip.notifications.models import Notification
from tuttitrip.notifications.schemas import NotificationRead, NotificationType
from tuttitrip.notifications.services import notification_service, stream_service
from tuttitrip.notifications.services.hub import (
    NotificationHub,
    Resync,
    connect_direct,
    load_notification,
)
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import database_url

pytestmark = pytest.mark.integration

PREFIX = "itest-stream|"


def run(
    test: Callable[
        [async_sessionmaker[AsyncSession], NotificationHub, str, str],
        Coroutine[None, None, None],
    ],
) -> None:
    """Run `test` with a real hub and two fresh users; clean their rows after."""
    mine, other = f"{PREFIX}{uuid.uuid4()}", f"{PREFIX}{uuid.uuid4()}"

    async def go() -> None:
        engine = create_async_engine(
            database_url(get_settings().database), poolclass=NullPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        hub = NotificationHub(
            connect_direct,
            lambda notification_id: load_notification(factory, notification_id),
            backoff=(0.05,),
        )
        try:
            await test(factory, hub, mine, other)
        finally:
            await hub.stop()
            async with factory.begin() as session:
                await session.execute(
                    delete(Notification).where(Notification.user_sub.like(f"{PREFIX}%"))
                )
            await engine.dispose()

    asyncio.run(go())


async def create(
    factory: async_sessionmaker[AsyncSession], *subs: str, key: str | None = None
) -> None:
    async with factory.begin() as session:
        await notification_service.notify(
            session,
            recipients=subs,
            type=NotificationType.PLAN_READY,
            dedupe_key=key,
        )


async def next_notification(stream: AsyncIterator[stream_service.StreamEvent]):  # ruff: ignore[missing-return-type-undocumented-public-function]
    async with asyncio.timeout(3):
        return await anext(stream)


def test_only_the_owner_gets_the_event_within_two_seconds() -> None:
    async def test(
        factory: async_sessionmaker[AsyncSession],
        hub: NotificationHub,
        mine: str,
        other: str,
    ) -> None:
        mine_stream = stream_service.events(hub, mine, token_exp=None, sessions=factory)
        other_stream = stream_service.events(
            hub, other, token_exp=None, sessions=factory
        )
        assert (await next_notification(mine_stream)).event == "ready"
        assert (await next_notification(other_stream)).event == "ready"
        await asyncio.sleep(0.3)  # the hub is listening
        started = time.monotonic()
        await create(factory, mine)
        event = await next_notification(mine_stream)
        assert event.event == "notification"
        assert time.monotonic() - started < 2
        assert isinstance(event.data, NotificationRead)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(other_stream), 0.4)
        await mine_stream.aclose()
        await other_stream.aclose()
        assert hub.subscriber_count == 0

    run(test)


def test_nothing_is_delivered_for_a_rolled_back_transaction() -> None:
    async def test(
        factory: async_sessionmaker[AsyncSession],
        hub: NotificationHub,
        mine: str,
        _other: str,
    ) -> None:
        stream = stream_service.events(hub, mine, token_exp=None, sessions=factory)
        await next_notification(stream)
        await asyncio.sleep(0.3)
        async with factory() as session:
            await notification_service.notify(
                session, recipients=[mine], type=NotificationType.PLAN_READY
            )
            await session.rollback()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(stream), 0.5)
        await stream.aclose()

    run(test)


def test_a_dropped_listener_connection_is_restored_with_a_resync() -> None:
    async def test(
        factory: async_sessionmaker[AsyncSession],
        hub: NotificationHub,
        mine: str,
        _other: str,
    ) -> None:
        stream = stream_service.events(hub, mine, token_exp=None, sessions=factory)
        await next_notification(stream)
        await asyncio.sleep(0.3)
        listener = cast("asyncpg.Connection", hub._connection)  # ruff: ignore[private-member-access]
        db = get_settings().database
        admin = await asyncpg.connect(
            user=db.user,
            password=db.password.get_secret_value(),
            host=db.host,
            port=db.port,
            database=db.name,
        )
        await admin.execute(
            "SELECT pg_terminate_backend($1)", listener.get_server_pid()
        )
        await admin.close()
        resync = await next_notification(stream)
        assert (resync.event, getattr(resync.data, "reason", None)) == (
            "resync",
            Resync("reconnected").reason,
        )
        await create(factory, mine)
        assert (await next_notification(stream)).event == "notification"
        await stream.aclose()

    run(test)


def test_a_reconnecting_client_gets_what_it_missed() -> None:
    async def test(
        factory: async_sessionmaker[AsyncSession],
        hub: NotificationHub,
        mine: str,
        _other: str,
    ) -> None:
        first = stream_service.events(hub, mine, token_exp=None, sessions=factory)
        assert (await next_notification(first)).event == "ready"
        await asyncio.sleep(0.3)
        await create(factory, mine, key="a")
        oldest = await next_notification(first)
        await first.aclose()
        await asyncio.sleep(0.01)
        await create(factory, mine, key="b")
        await asyncio.sleep(0.01)
        await create(factory, mine, key="c")
        again = stream_service.events(
            hub, mine, token_exp=None, last_event_id=oldest.id, sessions=factory
        )
        events = [await next_notification(again) for _ in range(3)]
        assert [e.event for e in events] == ["ready", "notification", "notification"]
        assert oldest.id not in {e.id for e in events}
        await again.aclose()

    run(test)
