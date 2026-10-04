"""Notifications on a real PostgreSQL: rollback, dedupe, actor and LISTEN/NOTIFY.

Needs a migrated database (`uv run alembic upgrade head`, see AGENTS.md).
"""

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import asyncpg
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tuttitrip.notifications.models import Notification
from tuttitrip.notifications.schemas import (
    NotificationAction,
    NotificationActionCode,
    NotificationType,
)
from tuttitrip.notifications.services import notification_service
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import database_url

pytestmark = pytest.mark.integration


@asynccontextmanager
async def sessions() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        database_url(get_settings().database), poolclass=NullPool
    )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def run_for_user(
    test: Callable[[async_sessionmaker[AsyncSession], str], Awaitable[None]],
) -> None:
    """Run `test` with a fresh user and delete that user's rows afterwards."""
    sub = f"test|{uuid.uuid4()}"

    async def go() -> None:
        async with sessions() as factory:
            try:
                await test(factory, sub)
            finally:
                async with factory.begin() as session:
                    await session.execute(
                        delete(Notification).where(Notification.user_sub.like("test|%"))
                    )

    asyncio.run(go())


async def count(factory: async_sessionmaker[AsyncSession], sub: str) -> int:
    async with factory() as session:
        return (
            await session.scalar(
                select(func.count()).where(Notification.user_sub == sub)
            )
        ) or 0


async def notify(
    session: AsyncSession,
    *recipients: str,
    params: dict[str, str] | None = None,
    actions: list[NotificationAction] | None = None,
    dedupe_key: str | None = None,
    actor: str | None = None,
) -> int:
    return await notification_service.notify(
        session,
        recipients=recipients,
        type=NotificationType.PLAN_READY,
        params=params,
        actions=actions or [],
        dedupe_key=dedupe_key,
        actor=actor,
    )


def test_rollback_leaves_no_row() -> None:
    async def test(factory: async_sessionmaker[AsyncSession], sub: str) -> None:
        async with factory() as session:
            assert await notify(session, sub) == 1
            await session.rollback()
        assert await count(factory, sub) == 0

    run_for_user(test)


def test_commit_keeps_the_row_with_actions_and_defaults() -> None:
    async def test(factory: async_sessionmaker[AsyncSession], sub: str) -> None:
        action = NotificationAction(
            code=NotificationActionCode.OPEN_PLAN, params={"day": "2"}
        )
        async with factory.begin() as session:
            await notify(session, sub, params={"a": "b"}, actions=[action])
        async with factory() as session:
            row = (
                await session.scalars(
                    select(Notification).where(Notification.user_sub == sub)
                )
            ).one()
        assert row.type == "plan_ready"
        assert row.params == {"a": "b"}
        assert row.actions == [{"code": "open_plan", "params": {"day": "2"}}]
        assert row.read_at is None
        assert row.created_at is not None

    run_for_user(test)


def test_same_dedupe_key_gives_one_row_per_recipient() -> None:
    async def test(factory: async_sessionmaker[AsyncSession], sub: str) -> None:
        async with factory.begin() as session:
            assert await notify(session, sub, dedupe_key="k") == 1
        async with factory.begin() as session:
            assert await notify(session, sub, dedupe_key="k") == 0
        assert await count(factory, sub) == 1

    run_for_user(test)


def test_without_a_key_every_call_creates_a_row() -> None:
    async def test(factory: async_sessionmaker[AsyncSession], sub: str) -> None:
        async with factory.begin() as session:
            await notify(session, sub)
            await notify(session, sub)
        assert await count(factory, sub) == 2

    run_for_user(test)


def test_actor_gets_no_notification() -> None:
    async def test(factory: async_sessionmaker[AsyncSession], sub: str) -> None:
        other = f"test|{uuid.uuid4()}"
        async with factory.begin() as session:
            assert await notify(session, sub, other, actor=sub) == 1
        assert await count(factory, sub) == 0
        assert await count(factory, other) == 1

    run_for_user(test)


def test_resolve_marks_every_notification_with_the_key_as_read() -> None:
    async def test(factory: async_sessionmaker[AsyncSession], sub: str) -> None:
        other = f"test|{uuid.uuid4()}"
        async with factory.begin() as session:
            await notify(session, sub, other, dedupe_key="p:1")
            await notify(session, sub, dedupe_key="p:2")
        async with factory.begin() as session:
            assert await notification_service.resolve(session, "p:1") == 2
            assert await notification_service.resolve(session, "p:1") == 0
        async with factory() as session:
            unread = await session.scalar(
                select(func.count()).where(
                    Notification.user_sub == sub, Notification.read_at.is_(None)
                )
            )
        assert unread == 1

    run_for_user(test)


def test_listener_gets_id_and_user_only_after_commit() -> None:
    async def test(factory: async_sessionmaker[AsyncSession], sub: str) -> None:
        db = get_settings().database
        listener = await asyncpg.connect(
            user=db.user,
            password=db.password.get_secret_value(),
            host=db.host,
            port=db.port,
            database=db.name,
        )
        events: asyncio.Queue[str] = asyncio.Queue()
        await listener.add_listener(
            "notifications", lambda _c, _pid, _ch, payload: events.put_nowait(payload)
        )
        try:
            async with factory() as session:
                await notify(session, sub)
                await asyncio.sleep(0.2)
                assert events.empty()
                await session.commit()
            payload = json.loads(await asyncio.wait_for(events.get(), timeout=2))
            async with factory() as session:
                row_id = await session.scalar(
                    select(Notification.id).where(Notification.user_sub == sub)
                )
            assert payload == {"id": str(row_id), "user_sub": sub}
            async with factory() as session:
                await notify(session, sub)
                await session.rollback()
            await asyncio.sleep(0.2)
            assert events.empty()
        finally:
            await listener.close()

    run_for_user(test)
