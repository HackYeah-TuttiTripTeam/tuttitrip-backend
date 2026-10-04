"""Notification list and counter over HTTP on a real PostgreSQL.

Needs a migrated database (`uv run alembic upgrade head`, see AGENTS.md).
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime, timedelta
from operator import itemgetter
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete
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
from tuttitrip.trips.models import Trip

pytestmark = pytest.mark.integration

PREFIX = "itest|"
START = datetime(2026, 9, 1, 12, tzinfo=UTC)
TYPES = ("member_joined", "veto_added", "plan_ready")
TRIP = uuid.uuid4()


def new_engine():  # ruff: ignore[missing-return-type-undocumented-public-function] - local helper for tests
    return create_async_engine(
        database_url(get_settings().database), poolclass=NullPool
    )


def seed(sub: str, count: int, trip_id: uuid.UUID | None = None) -> None:
    """Insert `count` notifications: types rotate, one per hour, every 3rd read."""

    async def go() -> None:
        engine = new_engine()
        async with async_sessionmaker(engine).begin() as session:
            if trip_id is not None:
                session.add(Trip(id=trip_id, owner_sub=sub, name="itest"))
                await session.flush()
            session.add_all(
                Notification(
                    user_sub=sub,
                    type=TYPES[i % 3],
                    trip_id=trip_id if i % 2 else None,
                    params={},
                    actions=[],
                    read_at=START if i % 3 == 0 else None,
                    created_at=START + timedelta(hours=i),
                )
                for i in range(count)
            )
        await engine.dispose()

    asyncio.run(go())


def wipe() -> None:
    async def go() -> None:
        engine = new_engine()
        async with async_sessionmaker(engine).begin() as session:
            await session.execute(
                delete(Notification).where(Notification.user_sub.like(f"{PREFIX}%"))
            )
            await session.execute(delete(Trip).where(Trip.owner_sub.like(f"{PREFIX}%")))
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
        Grant(Feature.NOTIFICATIONS, Access.READ)
    ]
    wipe()
    with TestClient(app) as test_client:
        yield test_client
    wipe()


def listing(client: TestClient, sub: str, **params: str | int) -> dict[str, Any]:
    response = client.get(
        path("list_notifications"), params=params, headers=bearer(sub=sub)
    )
    assert response.status_code == 200, response.text
    return response.json()


def mine() -> tuple[str, str]:
    return f"{PREFIX}{uuid.uuid4()}", f"{PREFIX}{uuid.uuid4()}"


def test_45_rows_paginate_into_three_pages(client: TestClient) -> None:
    sub, _ = mine()
    seed(sub, 45)
    page2 = listing(client, sub, page=2, size=20)
    assert (page2["total"], page2["pages"], len(page2["items"])) == (45, 3, 20)
    assert len(listing(client, sub, page=3, size=20)["items"]) == 5
    past = listing(client, sub, page=9, size=20)
    assert (past["items"], past["total"]) == ([], 45)


def test_each_user_sees_only_their_own(client: TestClient) -> None:
    first, second = mine()
    seed(first, 7)
    seed(second, 3)
    assert listing(client, first)["total"] == 7
    assert listing(client, second)["total"] == 3
    count = client.get(path("unread_count"), headers=bearer(sub=second))
    assert count.json() == {"count": 2}  # 3 rows, the first (i=0) is read


def test_filters_apply_together(client: TestClient) -> None:
    sub, _ = mine()
    seed(sub, 30, TRIP)
    created_from = (START + timedelta(hours=10)).isoformat()
    result = listing(
        client,
        sub,
        read="false",
        type="veto_added",
        trip_id=str(TRIP),
        created_from=created_from,
        created_to=(START + timedelta(hours=25)).isoformat(),
    )
    # Hours 10..24 with i % 3 == 1 (veto_added, unread) and odd i (has the trip).
    hours = [13, 19]
    got = sorted(datetime.fromisoformat(item["created_at"]) for item in result["items"])
    assert got == [START + timedelta(hours=h) for h in hours]
    assert result["total"] == 2


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_sort_by_type_is_stable_across_pages(
    client: TestClient, direction: str
) -> None:
    sub, _ = mine()
    seed(sub, 45)
    rows: list[dict[str, Any]] = []
    for page in (1, 2, 3):
        rows += listing(client, sub, sort="type", dir=direction, page=page, size=20)[
            "items"
        ]
    assert len({r["id"] for r in rows}) == 45
    keys = [(r["type"], r["created_at"]) for r in rows]
    by_type = sorted(
        keys, key=itemgetter(0), reverse=direction == "desc"
    )  # type order, whatever the tie-break
    assert [k[0] for k in keys] == [k[0] for k in by_type]
    for kind in TYPES:  # within a type: newest first, in both directions
        stamps = [k[1] for k in keys if k[0] == kind]
        assert stamps == sorted(stamps, reverse=True)


def test_unread_count_counts_only_unread(client: TestClient) -> None:
    sub, _ = mine()
    seed(sub, 10)  # i = 0, 3, 6, 9 are read
    response = client.get(path("unread_count"), headers=bearer(sub=sub))
    assert response.json() == {"count": 6}
