"""Plan generation end to end on a real PostgreSQL (local smoke step, not CI).

Run with `uv run pytest -m integration` after `alembic upgrade head`. The city
and places of the fixtures (backend#38) are written to the database and removed
at the end.
"""

import asyncio
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete

from tests.fixtures.city import CITY_SLUG, city, place_id, places
from tests.fixtures.personas import Persona, reference_family
from tests.fixtures.scenarios import reference
from tests.shared.fakes import authorize
from tuttitrip.main import create_app
from tuttitrip.places.models import City, Place, PlacePrice
from tuttitrip.places.schemas import PlaceRead
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker

pytestmark = pytest.mark.integration

HOST = AuthenticatedUser(sub=f"auth0|plan-{uuid.uuid4()}")


def _row(place: PlaceRead) -> Place:
    hours = place.hours
    return Place(
        id=place.id,
        city_slug=place.city_slug,
        name=place.name,
        category=place.category.value,
        tags=[t.value for t in place.tags],
        lat=place.lat,
        lon=place.lon,
        source_key=place.source_key,
        google_place_id=place.google_place_id,
        opening_hours=hours.opening_hours.model_dump(mode="json")
        if hours.opening_hours
        else None,
        hours_source_url=hours.source_url,
        hours_verified=hours.verified,
        hours_checked_at=hours.checked_at,
        typical_visit_min=place.typical_visit_min,
        segment_km=place.segment_km,
        transfer_min=place.transfer_min,
        queue_min=place.queue_min,
        stairs=place.stairs,
        wheelchair=place.wheelchair,
        indoor=place.indoor,
        iconic=place.iconic,
        cuisine=place.cuisine.value if place.cuisine else None,
        diet_tags=[t.value for t in place.diet_tags],
        amenities=[a.value for a in place.amenities],
        source=place.source.value,
    )


async def _seed() -> None:
    fixture_city = city()
    async with get_sessionmaker()() as session:
        if await session.get(City, CITY_SLUG) is None:
            session.add(City(**fixture_city.model_dump()))
            await session.flush()
        for place in places().values():
            if await session.get(Place, place.id) is not None:
                continue
            session.add(_row(place))
            await session.flush()
            session.add_all(
                PlacePrice(
                    place_id=place.id,
                    **price.model_dump(exclude={"age_min", "age_max"})
                    | {
                        "ticket_category": price.ticket_category.value,
                        "unit": price.unit.value,
                        "age_min": price.age_min,
                        "age_max": price.age_max,
                    },
                )
                for price in place.prices
            )
        await session.commit()


async def _unseed() -> None:
    async with get_sessionmaker()() as session:
        # Prices and ratings go with the places (ON DELETE CASCADE).
        await session.execute(delete(Place).where(Place.city_slug == CITY_SLUG))
        await session.execute(delete(City).where(City.slug == CITY_SLUG))
        await session.commit()


async def _add_person(http: httpx.AsyncClient, base: str, persona: Persona) -> str:
    made = await http.post(
        f"{base}/profiles",
        json=persona.profile.model_dump(mode="json", exclude_none=True),
    )
    assert made.status_code == 201, made.text
    profile_id = str(made.json()["id"])
    prefs = await http.put(
        f"{base}/profiles/{profile_id}/preferences",
        json=persona.preferences.model_dump(mode="json"),
    )
    assert prefs.status_code == 200, prefs.text
    for key, rating in persona.ratings.items():
        voted = await http.put(
            f"{base}/profiles/{profile_id}/ratings/{place_id(key)}",
            json=rating.model_dump(mode="json", exclude_none=True),
        )
        assert voted.status_code == 200, voted.text
    return profile_id


async def _scenario(app: FastAPI) -> None:  # ruff: ignore[too-many-locals] - one story
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        scenario = reference()
        trip = await http.post(
            "/api/v1/trips",
            json=scenario.trip.model_dump(mode="json", exclude_none=True),
        )
        assert trip.status_code == 201, trip.text
        base = f"/api/v1/trips/{trip.json()['id']}"
        plans = f"{base}/plans"
        try:
            empty = await http.get(f"{plans}/latest")
            assert empty.status_code == 404

            ids = {}
            for persona in reference_family().people:
                ids[persona.key] = await _add_person(http, base, persona)

            first = await http.post(plans)
            assert first.status_code == 201, first.text
            body = first.json()
            assert body["version"] == 1
            assert len(body["plan_hash"]) == 12
            assert body["telemetry"]["solo_runs"] == body["fairness"]["group_size"]
            assert all(len(p["domains"]) == 5 for p in body["fairness"]["per_person"])
            assert all(
                p["u"] <= p["u_star"] + 1e-6 for p in body["fairness"]["per_person"]
            )
            assert any(
                d["not_applicable"]
                for p in body["fairness"]["per_person"]
                for d in p["domains"]
            )

            again = await http.post(plans)
            assert again.status_code == 200
            assert again.json() == body

            latest = await http.get(f"{plans}/latest")
            assert latest.json() == body
            by_id = await http.get(f"{plans}/{body['id']}")
            assert by_id.json() == body
            assert (await http.get(f"{plans}/{uuid.uuid4()}")).status_code == 404

            # A veto on a place of the plan gives a new version without it.
            chosen = [i["place_id"] for d in body["days"] for i in d["items"]]
            veto = await http.post(
                f"{base}/vetoes",
                json={"profile_id": ids["babcia"], "place_id": chosen[0]},
            )
            assert veto.status_code == 201, veto.text
            after = await http.post(plans)
            assert after.status_code == 201
            assert after.json()["version"] == 2
            assert chosen[0] not in [
                i["place_id"] for d in after.json()["days"] for i in d["items"]
            ]

            # The alpha slider is part of the input.
            other = await http.post(plans, json={"alpha": 2})
            assert other.status_code == 201
            assert other.json()["params"]["alpha"] == 2
            assert other.json()["version"] == 3

            # Racing requests for an input nobody stored yet never give a 500.
            racing = await asyncio.gather(
                *(http.post(plans, json={"alpha": 0.5}) for _ in range(4))
            )
            assert {r.status_code for r in racing} <= {200, 201}
            assert sum(r.status_code == 201 for r in racing) == 1
            assert len({r.json()["id"] for r in racing}) == 1
            assert (await http.get(f"{plans}/latest")).json()["version"] == 4
        finally:
            await http.delete(base)


def test_plan_versions_end_to_end() -> None:
    app = create_app()
    authorize(app, HOST)

    async def run() -> None:
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
        try:
            await _seed()
            try:
                await _scenario(app)
            finally:
                await _unseed()
        finally:
            await dispose_engine()

    asyncio.run(run())
