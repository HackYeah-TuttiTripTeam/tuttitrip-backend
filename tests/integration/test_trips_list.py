"""``GET /trips`` against a real PostgreSQL (the CI runs without one: skipped)."""

import asyncio
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.db.session import database_url
from tuttitrip.trips.models import Trip, TripMember
from tuttitrip.trips.schemas import MemberStatus, TripRole

pytestmark = pytest.mark.integration

type Field = date | datetime | str | None

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC).replace(tzinfo=None)


class World:
    """A caller and a stranger, with the trips each belongs to."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.me = AuthenticatedUser(sub=f"auth0|me-{uuid.uuid4()}")
        self.other = AuthenticatedUser(sub=f"auth0|other-{uuid.uuid4()}")
        self.trip_ids: list[uuid.UUID] = []

    async def add(
        self,
        name: str,
        role: TripRole = TripRole.HOST,
        status: MemberStatus = MemberStatus.CONFIRMED,
        **fields: Field,
    ) -> Trip:
        """Insert a trip of the caller (``me``) with the given role."""
        fields.setdefault("created_at", NOW - timedelta(minutes=len(self.trip_ids)))
        async with self.sessions() as session:
            trip = Trip(owner_sub=self.me.sub, name=name, **fields)
            session.add(trip)
            await session.flush()
            session.add(
                TripMember(
                    trip_id=trip.id, user_sub=self.me.sub, role=role, status=status
                )
            )
            await session.commit()
        self.trip_ids.append(trip.id)
        return trip

    async def add_foreign(self, name: str) -> None:
        """Insert a trip that only ``other`` belongs to."""
        async with self.sessions() as session:
            trip = Trip(owner_sub=self.other.sub, name=name)
            session.add(trip)
            await session.flush()
            session.add(
                TripMember(trip_id=trip.id, user_sub=self.other.sub, role=TripRole.HOST)
            )
            await session.commit()
        self.trip_ids.append(trip.id)

    @asynccontextmanager
    async def client(
        self, user: AuthenticatedUser
    ) -> AsyncGenerator[httpx.AsyncClient]:
        """An HTTP client acting as ``user``, on the real database."""
        app = create_app()
        authorize(app, user)

        transport = httpx.ASGITransport(app=app)
        async with (
            self.sessions() as session,
            httpx.AsyncClient(transport=transport, base_url="http://t") as client,
        ):
            app.dependency_overrides[get_session] = lambda: session
            yield client

    async def get(self, user: AuthenticatedUser, params: Any = None) -> dict[str, Any]:  # ruff: ignore[any-type]
        """Call the list endpoint and return the JSON of a 200."""
        async with self.client(user) as client:
            response = await client.get(path("list_trips"), params=params)
        assert response.status_code == 200, response.text
        return response.json()


def with_world(
    test: Callable[[World], Awaitable[None]],
) -> Callable[[], None]:
    """Run an async test body against a fresh `World`, then clean up."""

    def run() -> None:
        async def main() -> None:
            engine = create_async_engine(
                database_url(get_settings().database), poolclass=NullPool
            )
            world = World(engine)
            try:
                try:
                    async with engine.connect():
                        pass
                except OperationalError, OSError:
                    pytest.skip("no PostgreSQL reachable")
                await test(world)
            finally:
                async with world.sessions() as session:
                    await session.execute(
                        delete(Trip).where(Trip.id.in_(world.trip_ids))
                    )
                    await session.commit()
                await engine.dispose()

        asyncio.run(main())

    run.__name__ = getattr(test, "__name__", "run")
    return run


def names(body: dict[str, Any]) -> list[str]:
    """Trip names of a page, in order."""
    return [item["name"] for item in body["items"]]


@with_world
async def test_pages_and_totals(world: World) -> None:
    for i in range(45):
        await world.add(f"Trip {i:02}")
    body = await world.get(world.me, {"page": 2, "size": 20})
    assert (len(body["items"]), body["total"], body["pages"], body["page"]) == (
        20,
        45,
        3,
        2,
    )
    last = await world.get(world.me, {"page": 3, "size": 20})
    assert (len(last["items"]), last["total"]) == (5, 45)
    beyond = await world.get(world.me, {"page": 9, "size": 20})
    assert (beyond["items"], beyond["total"]) == ([], 45)


@with_world
async def test_default_order_is_newest_first(world: World) -> None:
    await world.add("old", created_at=NOW - timedelta(days=2))
    await world.add("new", created_at=NOW)
    assert names(await world.get(world.me)) == ["new", "old"]


@with_world
async def test_scoped_to_the_caller(world: World) -> None:
    await world.add("mine")
    await world.add_foreign("theirs")
    assert names(await world.get(world.me)) == ["mine"]
    assert names(await world.get(world.other)) == ["theirs"]


@with_world
async def test_filters_combine_and_dates_sort_last_both_ways(world: World) -> None:
    await world.add("Kraków late", city_slug="krakow", start_date=date(2026, 12, 1))
    await world.add("Kraków early", city_slug="krakow", start_date=date(2026, 11, 5))
    await world.add("Kraków undated", city_slug="krakow")
    await world.add("Kraków old", city_slug="krakow", start_date=date(2026, 5, 1))
    await world.add("Kraków guest", TripRole.MEMBER, city_slug="krakow")
    await world.add("Gdańsk", city_slug="gdansk", start_date=date(2026, 11, 9))
    base = {"role": "host", "start_from": "2026-11-01", "city": "krakow"}
    asc = await world.get(world.me, {**base, "sort": "start_date", "dir": "asc"})
    assert names(asc) == ["Kraków early", "Kraków late"]
    allv = {"city": "krakow", "sort": "start_date"}
    asc = await world.get(world.me, {**allv, "dir": "asc"})
    assert set(names(asc)[-2:]) == {"Kraków undated", "Kraków guest"}  # NULLs last
    desc = await world.get(world.me, {**allv, "dir": "desc"})
    assert names(desc)[0] == "Kraków late"
    assert set(names(desc)[-2:]) == {"Kraków undated", "Kraków guest"}
    assert asc["total"] == desc["total"] == 5


@with_world
async def test_roles_are_repeatable_and_my_role_is_reported(world: World) -> None:
    await world.add("h", TripRole.HOST)
    await world.add("c", TripRole.CO_HOST)
    await world.add("m", TripRole.MEMBER)
    body = await world.get(world.me, [("role", "host"), ("role", "member")])
    assert {(i["name"], i["my_role"]) for i in body["items"]} == {
        ("h", "host"),
        ("m", "member"),
    }


@with_world
async def test_q_matches_name_or_destination_ignoring_case_and_wildcards(
    world: World,
) -> None:
    await world.add("Wypad do KRAkowa")
    await world.add("Weekend", destination="Okolice kraKOWA")
    await world.add("Gdańsk", destination="Morze")
    await world.add("100% fun")
    body = await world.get(world.me, {"q": "kra"})
    assert sorted(names(body)) == ["Weekend", "Wypad do KRAkowa"]
    assert names(await world.get(world.me, {"q": "%"})) == ["100% fun"]
    assert names(await world.get(world.me, {"q": "_"})) == []


@with_world
async def test_kind_splits_outings_from_trips(world: World) -> None:
    await world.add("day", start_date=date(2026, 11, 1), end_date=date(2026, 11, 1))
    await world.add("long", start_date=date(2026, 11, 1), end_date=date(2026, 11, 3))
    await world.add("undated")
    assert names(await world.get(world.me, {"kind": "outing"})) == ["day"]
    both = await world.get(world.me, {"kind": "trip", "sort": "name", "dir": "asc"})
    assert names(both) == ["long", "undated"]


@with_world
async def test_pages_are_stable_with_equal_sort_keys(world: World) -> None:
    same = NOW - timedelta(days=1)
    for i in range(7):
        await world.add(f"same {i}", created_at=same)
    seen: list[str] = []
    for page in (1, 2, 3):
        seen += [
            i["id"]
            for i in (await world.get(world.me, {"page": page, "size": 3}))["items"]
        ]
    assert len(seen) == len(set(seen)) == 7


@with_world
async def test_name_sort_ignores_case(world: World) -> None:
    for name in ("b", "A", "c"):
        await world.add(name)
    body = await world.get(world.me, {"sort": "name", "dir": "asc"})
    assert names(body) == ["A", "b", "c"]


@with_world
async def test_combined_filter_is_stable_across_a_page_boundary(world: World) -> None:
    for i in range(5):
        await world.add(
            f"k{i}", city_slug="krakow", start_date=date(2026, 11, 1 + i % 2)
        )
    await world.add("undated", city_slug="krakow")
    await world.add("guest", TripRole.MEMBER, city_slug="krakow", start_date=NOW.date())
    await world.add("other city", city_slug="gdansk", start_date=date(2026, 11, 2))
    params = {
        "role": "host",
        "start_from": "2026-11-01",
        "city": "krakow",
        "sort": "start_date",
        "dir": "asc",
        "size": 2,
    }
    seen: list[str] = []
    for page in (1, 2, 3):
        body = await world.get(world.me, {**params, "page": page})
        assert body["total"] == 5
        seen += [i["name"] for i in body["items"]]
    assert sorted(seen) == [f"k{i}" for i in range(5)]
    assert len(seen) == len(set(seen))
    again = []
    for page in (1, 2, 3):
        body = await world.get(world.me, {**params, "page": page})
        again += [i["name"] for i in body["items"]]
    assert again == seen


@with_world
async def test_when_splits_history_from_upcoming_and_status_finds_pending(
    world: World,
) -> None:
    today = datetime.now(UTC).date()
    day = timedelta(days=1)
    await world.add("past", start_date=today - 3 * day, end_date=today - day)
    await world.add("today", start_date=today, end_date=today)
    await world.add("future", start_date=today + day, end_date=today + 2 * day)
    await world.add("undated")
    await world.add(
        "invited",
        TripRole.MEMBER,
        MemberStatus.PENDING,
        start_date=today + day,
        end_date=today + day,
    )
    past = await world.get(world.me, {"when": "past"})
    assert names(past) == ["past"]
    upcoming = await world.get(world.me, {"when": "upcoming", "sort": "name"})
    assert set(names(upcoming)) == {"today", "future", "undated", "invited"}
    assert upcoming["total"] == 4
    pending = await world.get(world.me, {"status": "pending"})
    assert names(pending) == ["invited"]
    assert pending["items"][0]["my_status"] == "pending"
    both = await world.get(world.me, {"status": "confirmed", "when": "upcoming"})
    assert "invited" not in names(both)
    assert {i["my_status"] for i in both["items"]} == {"confirmed"}
