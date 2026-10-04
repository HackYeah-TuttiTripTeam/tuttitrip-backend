"""The sample trip on a real PostgreSQL (``-m integration``)."""

import asyncio
import uuid
from collections.abc import Coroutine, Iterator
from datetime import date
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

import tuttitrip.main  # ruff: ignore[unused-import]  # registers the erasers
from tests.fixtures.city import CITY_SLUG
from tests.shared.db_seed import seed_city, unseed_city
from tests.shared.fakes import authorize
from tuttitrip.demo.logic.dataset import DEMO_TRIPS
from tuttitrip.demo.models import SampleTripGrant
from tuttitrip.demo.services import demo_service
from tuttitrip.main import create_app
from tuttitrip.shared.admin_users.services import erasure
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker
from tuttitrip.trips import db as trips_db
from tuttitrip.trips.models import Trip

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("sample_city_setting")]

API = "/api/v1"
TODAY = date(2026, 10, 4)


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(sub=f"auth0|sample-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def sample_city_setting(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TUTTITRIP_SAMPLE_TRIP__CITY_SLUG", CITY_SLUG)
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


async def _forget(subs: list[str]) -> None:
    async with get_sessionmaker()() as session:
        for sub in subs:
            await trips_db.delete_trips_owned_by(session, sub)
            await erasure.erase(session, sub)
        await session.commit()


def _run(*subs: str, scenario: Coroutine[object, object, None]) -> None:
    async def go() -> None:
        await seed_city()
        try:
            await scenario
        finally:
            await _forget(list(subs))
            await unseed_city()
            await dispose_engine()

    asyncio.run(go())


async def _list(
    app: FastAPI, user: AuthenticatedUser, language: str | None = None
) -> list[dict[str, object]]:
    authorize(app, user)
    headers = {"Accept-Language": language} if language else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        reply = await http.get(f"{API}/trips", headers=headers)
    assert reply.status_code == 200, reply.text
    return cast("list[dict[str, object]]", reply.json()["items"])


async def _get(app: FastAPI, user: AuthenticatedUser, url: str) -> httpx.Response:
    authorize(app, user)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.get(f"{API}{url}")


def test_first_trip_list_creates_one_complete_sample_trip() -> None:
    app, user = create_app(), _user()

    async def scenario() -> None:
        first = await _list(app, user)
        assert len(first) == 1
        trip = first[0]
        assert trip["is_sample"] is True
        assert str(trip["name"]).startswith("Przykład: ")
        assert trip["my_role"] == "host"
        base = f"/trips/{trip['id']}"

        plan = (await _get(app, user, f"{base}/plans/latest")).json()
        assert len(plan["plan_hash"]) == 12
        assert plan["fairness"]["group_size"] == 4
        profiles = (await _get(app, user, f"{base}/profiles")).json()
        assert {p["display_name"] for p in profiles} == {
            "Ola",
            "Kasia",
            "Tomek",
            "Babcia Halina",
        }
        expenses = (await _get(app, user, f"{base}/expenses")).json()
        assert len(expenses["items"]) == 3
        settlement = (await _get(app, user, f"{base}/expenses/settlement")).json()
        assert settlement["transfers"]
        unread = (await _get(app, user, "/notifications/unread-count")).json()
        assert unread["count"] >= 1

        again = await _list(app, user)
        assert [t["id"] for t in again] == [trip["id"]]

    _run(user.sub, scenario=scenario())


def test_a_deleted_sample_does_not_come_back() -> None:
    app, user = create_app(), _user()

    async def scenario() -> None:
        (trip,) = await _list(app, user)
        authorize(app, user)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as http:
            gone = await http.delete(f"{API}/trips/{trip['id']}")
        assert gone.status_code == 204
        assert await _list(app, user) == []

    _run(user.sub, scenario=scenario())


def test_parallel_first_requests_make_one_trip() -> None:
    app, user = create_app(), _user()

    async def scenario() -> None:
        results = await asyncio.gather(*(_list(app, user) for _ in range(3)))
        assert {len(items) for items in results} == {1}
        async with get_sessionmaker()() as session:
            count = await session.scalar(
                select(func.count()).select_from(Trip).where(Trip.owner_sub == user.sub)
            )
        assert count == 1

    _run(user.sub, scenario=scenario())


def test_plan_hash_is_stable_and_english_follows_accept_language() -> None:
    app, polish, english = create_app(), _user(), _user()

    async def scenario() -> None:
        (pl,) = await _list(app, polish)
        (en,) = await _list(app, english, "en-GB,en;q=0.8")
        assert str(en["name"]).startswith("Sample: ")
        hashes = [
            (await _get(app, u, f"/trips/{t['id']}/plans/latest")).json()["plan_hash"]
            for u, t in ((polish, pl), (english, en))
        ]
        assert hashes[0] == hashes[1]

    _run(polish.sub, english.sub, scenario=scenario())


def test_the_setting_turns_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TUTTITRIP_SAMPLE_TRIP__ENABLED", "false")
    get_settings.cache_clear()
    app, user = create_app(), _user()

    async def scenario() -> None:
        assert await _list(app, user) == []
        async with get_sessionmaker()() as session:
            assert await session.get(SampleTripGrant, user.sub) is None

    _run(user.sub, scenario=scenario())


def test_erasure_removes_the_sample_trip_and_the_mark() -> None:
    app, user = create_app(), _user()

    async def scenario() -> None:
        await _list(app, user)
        async with get_sessionmaker()() as session:
            counts = await erasure.erase(session, user.sub)
            await session.commit()
        assert counts["trips_deleted"] == 1
        assert counts["sample_trip_marks_removed"] == 1
        async with get_sessionmaker()() as session:
            owned = await session.scalar(
                select(func.count()).select_from(Trip).where(Trip.owner_sub == user.sub)
            )
            assert owned == 0
            assert await session.get(SampleTripGrant, user.sub) is None

    _run(user.sub, scenario=scenario())


def test_the_demo_reset_adds_the_sample_trip_to_the_demo_trips() -> None:
    user = _user()

    async def scenario() -> None:
        created = await demo_service.run_reset(get_engine(), user.sub, TODAY)
        assert created == len(DEMO_TRIPS)
        async with get_sessionmaker()() as session:
            names = (
                await session.scalars(
                    select(Trip.name).where(Trip.owner_sub == user.sub, Trip.is_sample)
                )
            ).all()
            total = await session.scalar(
                select(func.count()).select_from(Trip).where(Trip.owner_sub == user.sub)
            )
        assert names == ["Przykład: Warszawa z rodziną"]
        assert total == len(DEMO_TRIPS) + 1
        # A second reset replaces everything, the sample included.
        await demo_service.run_reset(get_engine(), user.sub, TODAY)
        async with get_sessionmaker()() as session:
            total = await session.scalar(
                select(func.count()).select_from(Trip).where(Trip.owner_sub == user.sub)
            )
        assert total == len(DEMO_TRIPS) + 1

    _run(user.sub, scenario=scenario())
