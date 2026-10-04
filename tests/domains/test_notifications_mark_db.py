"""Marking notifications over HTTP on a real PostgreSQL.

Needs a migrated database (`uv run alembic upgrade head`, see AGENTS.md).
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.shared.paths import path
from tests.shared.tokens import bearer, make_verifier
from tuttitrip.main import create_app
from tuttitrip.notifications.models import Notification
from tuttitrip.shared.auth.api import get_token_verifier
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.db.session import database_url
from tuttitrip.shared.permissions.api import get_user_grants
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature

pytestmark = pytest.mark.integration

PREFIX = "itest-mark|"
START = datetime(2026, 9, 1, 12, tzinfo=UTC)
READ_AT = datetime(2026, 9, 2, 12, tzinfo=UTC)


def new_engine():  # ruff: ignore[missing-return-type-undocumented-public-function] - local helper for tests
    return create_async_engine(
        database_url(get_settings().database), poolclass=NullPool
    )


def seed(sub: str, types: list[str], *, read: bool = False) -> list[uuid.UUID]:
    """Insert one notification per type, oldest first; return their ids."""
    ids = [uuid.uuid4() for _ in types]
    rows = [
        Notification(
            id=ids[i],
            user_sub=sub,
            type=kind,
            params={},
            actions=[],
            read_at=READ_AT if read else None,
            created_at=START + timedelta(hours=i),
        )
        for i, kind in enumerate(types)
    ]

    async def go() -> None:
        engine = new_engine()
        async with async_sessionmaker(engine).begin() as session:
            session.add_all(rows)
        await engine.dispose()

    asyncio.run(go())
    return ids


def read_states(sub: str) -> dict[uuid.UUID, bool]:
    async def go() -> dict[uuid.UUID, bool]:
        engine = new_engine()
        async with async_sessionmaker(engine)() as session:
            rows = await session.execute(
                select(Notification.id, Notification.read_at).where(
                    Notification.user_sub == sub
                )
            )
            result: dict[uuid.UUID, bool] = {
                row.id: row.read_at is not None for row in rows
            }
        await engine.dispose()
        return result

    return asyncio.run(go())


def wipe() -> None:
    async def go() -> None:
        engine = new_engine()
        async with async_sessionmaker(engine).begin() as session:
            await session.execute(
                delete(Notification).where(Notification.user_sub.like(f"{PREFIX}%"))
            )
        await engine.dispose()

    asyncio.run(go())


@pytest.fixture
def client() -> Iterator[TestClient]:
    app: FastAPI = create_app()
    verifier = make_verifier()

    async def real_session() -> AsyncGenerator[AsyncSession]:
        engine = new_engine()
        session = async_sessionmaker(engine, expire_on_commit=False)()
        try:
            yield session
        finally:
            await session.close()
            await engine.dispose()

    app.dependency_overrides[get_token_verifier] = lambda: verifier
    app.dependency_overrides[get_session] = real_session
    app.dependency_overrides[get_user_grants] = lambda: [
        Grant(Feature.NOTIFICATIONS, Access.WRITE)
    ]
    wipe()
    with TestClient(app) as test_client:
        yield test_client
    wipe()


def user() -> str:
    return f"{PREFIX}{uuid.uuid4()}"


def mark(client: TestClient, sub: str, body: dict[str, Any]) -> int:
    response = client.post(
        path("mark_notifications"), json=body, headers=bearer(sub=sub)
    )
    assert response.status_code == 200, response.text
    return int(response.json()["updated"])


def unread(client: TestClient, sub: str) -> int:
    response = client.get(path("unread_count"), headers=bearer(sub=sub))
    return int(response.json()["count"])


def test_ids_mark_only_the_chosen_rows(client: TestClient) -> None:
    sub = user()
    a, b, c = seed(sub, ["plan_ready"] * 3)
    assert mark(client, sub, {"read": True, "ids": [str(a), str(b)]}) == 2
    assert read_states(sub) == {a: True, b: True, c: False}
    assert unread(client, sub) == 1


def test_marking_is_idempotent_and_counts_only_changes(client: TestClient) -> None:
    sub = user()
    a, b = seed(sub, ["plan_ready"] * 2)
    body = {"read": True, "ids": [str(a), str(b)]}
    assert mark(client, sub, body) == 2
    assert mark(client, sub, body) == 0
    assert mark(client, sub, {"read": True, "ids": [str(a)]}) == 0


def test_unread_brings_rows_back_and_the_counter_grows(client: TestClient) -> None:
    sub = user()
    a, b = seed(sub, ["plan_ready"] * 2, read=True)
    assert unread(client, sub) == 0
    assert mark(client, sub, {"read": False, "ids": [str(a)]}) == 1
    assert unread(client, sub) == 1
    assert read_states(sub) == {a: False, b: True}


def test_read_keeps_the_original_read_at(client: TestClient) -> None:
    sub = user()
    seed(sub, ["plan_ready"], read=True)
    assert mark(client, sub, {"read": True, "filters": {}}) == 0

    async def read_at() -> datetime | None:
        engine = new_engine()
        async with async_sessionmaker(engine)() as session:
            value = await session.scalar(
                select(Notification.read_at).where(Notification.user_sub == sub)
            )
        await engine.dispose()
        return value

    assert asyncio.run(read_at()) == READ_AT


def test_a_filter_marks_everything_matching_beyond_the_first_page(
    client: TestClient,
) -> None:
    sub = user()
    seed(sub, ["veto_added"] * 130 + ["plan_ready"] * 5)
    updated = mark(client, sub, {"read": True, "filters": {"type": ["veto_added"]}})
    assert updated == 130
    assert unread(client, sub) == 5


def test_an_empty_filter_marks_all_and_filters_combine(client: TestClient) -> None:
    sub = user()
    seed(sub, ["veto_added", "plan_ready", "veto_added"])
    created_to = (START + timedelta(hours=2)).isoformat()
    body = {"read": True, "filters": {"type": ["veto_added"], "created_to": created_to}}
    assert mark(client, sub, body) == 1
    assert mark(client, sub, {"read": True, "filters": {}}) == 2
    assert unread(client, sub) == 0


def test_foreign_ids_and_foreign_rows_are_untouched(client: TestClient) -> None:
    me, other = user(), user()
    mine = seed(me, ["plan_ready"])
    theirs = seed(other, ["plan_ready"] * 2)
    assert mark(client, me, {"read": True, "ids": [str(i) for i in theirs]}) == 0
    assert read_states(other) == {theirs[0]: False, theirs[1]: False}
    assert mark(client, me, {"read": True, "filters": {}}) == 1
    assert read_states(other) == {theirs[0]: False, theirs[1]: False}
    assert read_states(me) == {mine[0]: True}
