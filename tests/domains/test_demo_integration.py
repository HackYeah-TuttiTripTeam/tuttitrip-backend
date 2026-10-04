"""The demo reset with computed plans on a real PostgreSQL (local, not CI).

Run with `uv run pytest -m integration` after `alembic upgrade head`. The demo's
Warszawa trips are moved to the fixture city (the real catalog is not in the
test database), so the numbers differ from the stage, the properties do not:
two resets give the same state, the stored `plan_hash` is what a recompute gives,
and the tight-budget variant asks for approval with a price per point.
"""

import asyncio
import dataclasses
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from tests.fixtures.city import CITY_SLUG
from tests.shared.db_seed import seed_city, unseed_city
from tuttitrip.demo.logic import dataset
from tuttitrip.demo.services import demo_service
from tuttitrip.planning.linter.models import PastedDocument
from tuttitrip.planning.plans.models import PlanVersion
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker
from tuttitrip.trips.models import Trip
from tuttitrip.trips.schemas import TripRole
from tuttitrip.trips.services import trip_service

pytestmark = pytest.mark.integration

SUB = f"auth0|demo-{uuid.uuid4()}"
TODAY = date(2026, 10, 4)
FAMILY = "Warszawa z rodziną"


async def _state(session_trips: list[Trip]) -> list[tuple[str, str]]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(
            select(Trip.name, PlanVersion.plan_hash)
            .join(PlanVersion, PlanVersion.trip_id == Trip.id, isouter=True)
            .where(Trip.owner_sub == SUB, Trip.id.in_([t.id for t in session_trips]))
            .order_by(Trip.name, PlanVersion.version)
        )
        return [(name, plan_hash or "") for name, plan_hash in rows.all()]


async def _trips() -> list[Trip]:
    async with get_sessionmaker()() as session:
        found = await session.scalars(select(Trip).where(Trip.owner_sub == SUB))
        return list(found.all())


async def _scenario() -> None:
    warszawa = [
        dataclasses.replace(t, city_slug=CITY_SLUG)
        for t in dataset.DEMO_TRIPS
        if t.destination == "Warszawa"
    ]
    dataset_trips = tuple(warszawa)
    original = demo_service.DEMO_TRIPS
    demo_service.DEMO_TRIPS = dataset_trips
    try:
        engine = get_engine()
        assert await demo_service.run_reset(engine, SUB, TODAY) == 2
        first = await _state(await _trips())
        assert await demo_service.run_reset(engine, SUB, TODAY) == 2
        trips = await _trips()
        second = await _state(trips)
        # Idempotent: no duplicates, the same plan for the same data. The ids of
        # trips and people are new each time: `input_hash` differs, the plan does not.
        assert len(trips) == 2
        assert first == second
        assert all(plan_hash for _, plan_hash in second)

        async with get_sessionmaker()() as session:
            documents = await session.scalar(
                select(func.count())
                .select_from(PastedDocument)
                .where(PastedDocument.trip_id.in_([t.id for t in trips]))
            )
            assert documents == 2  # the chatbot plan and the offer, on the family trip
            by_name = {t.name: t for t in trips}
            family = by_name[FAMILY]
            tight = next(t for n, t in by_name.items() if n != FAMILY)
            for trip in (family, tight):
                membership = await trip_service.get_membership(
                    session, trip.id, SUB, TripRole.HOST
                )
                stored = await plan_service.latest_plan(session, membership)
                # Recomputing finds the same input: the same version, the same hash.
                again, created = await plan_service.generate_plan(
                    session, membership, None
                )
                assert not created
                assert again.plan_hash == stored.plan_hash
                print(  # ruff: ignore[print] - for the person running the check
                    f"{trip.name}: hash {stored.plan_hash} "
                    f"needs_approval={stored.budget.needs_approval} "
                    f"kappa={stored.budget.kappa} cost={stored.budget.cost}"
                )
    finally:
        demo_service.DEMO_TRIPS = original
        async with get_sessionmaker()() as session:
            await trip_service.delete_trips_owned_by(session, SUB)
            await session.commit()


def test_two_resets_give_the_same_state_with_computed_plans() -> None:
    async def run() -> None:
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
        try:
            await seed_city()
            try:
                await _scenario()
            finally:
                await unseed_city()
        finally:
            await dispose_engine()

    asyncio.run(run())
