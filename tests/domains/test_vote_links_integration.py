"""Voting links end to end on a real PostgreSQL (local smoke step, not CI).

Run with `uv run pytest -m integration` after `alembic upgrade head`; the
database comes from the `TUTTITRIP_DATABASE__*` variables.
"""

import asyncio
import uuid

import httpx
import pytest
from fastapi import FastAPI

from tests.shared.fakes import authorize
from tuttitrip.main import create_app
from tuttitrip.places.models import City, Place
from tuttitrip.profiles.feedback import db as feedback_db
from tuttitrip.profiles.feedback.models import PlaceRating, PlaceVeto
from tuttitrip.profiles.feedback.schemas import RatingValue, ReasonCode, link_author
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker

pytestmark = pytest.mark.integration

HOST = AuthenticatedUser(sub=f"auth0|it-{uuid.uuid4()}")
CITY = f"it-{uuid.uuid4().hex[:8]}"


async def _seed_place(name: str) -> uuid.UUID:
    async with get_sessionmaker()() as session:
        if await session.get(City, CITY) is None:
            session.add(
                City(
                    slug=CITY,
                    name="Test",
                    country="PL",
                    timezone="Europe/Warsaw",
                    currency="PLN",
                    center_lat=50.0,
                    center_lon=19.9,
                    bbox_south=49.9,
                    bbox_west=19.8,
                    bbox_north=50.1,
                    bbox_east=20.1,
                )
            )
            await session.flush()
        place = Place(
            city_slug=CITY,
            name=name,
            category="museum",
            lat=50.0,
            lon=19.9,
            source="sheet",
            source_key=f"{name}-{uuid.uuid4()}",
        )
        session.add(place)
        await session.commit()
        return place.id


async def _cleanup(place_ids: list[uuid.UUID]) -> None:
    """Delete the places; their ratings and vetoes go with them (CASCADE)."""
    async with get_sessionmaker()() as session:
        for place_id in place_ids:
            place = await session.get(Place, place_id)
            if place is not None:
                await session.delete(place)
        await session.commit()


async def _scenario(app: FastAPI) -> None:  # ruff: ignore[too-many-statements, too-many-locals] - one story, read top-down
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        trip = (await http.post("/api/v1/trips", json={"name": "IT"})).json()
        trip_id = trip["id"]
        base = f"/api/v1/trips/{trip_id}"
        grandma = (
            await http.post(
                f"{base}/profiles", json={"display_name": "Babcia", "age": 80}
            )
        ).json()["id"]
        museum = await _seed_place("Muzeum")
        park = await _seed_place("Park")
        try:
            # Five working profile tokens (#79) never turn a new link into a 500.
            for _ in range(5):
                made = await http.post(
                    f"{base}/profiles/{grandma}/access-tokens", json={}
                )
                assert made.status_code == 201
            first = await http.post(f"{base}/vote-links", json={"profile_id": grandma})
            assert first.status_code == 201
            assert first.headers["cache-control"] == "no-store"
            token_1 = first.json()["token"]
            assert first.json()["url"] == f"/glos#t={token_1}"

            # The token opens the voting access check; nothing else does.
            ok = await http.get(
                "/api/v1/vote/access", headers={"X-Access-Token": token_1}
            )
            assert ok.status_code == 200
            assert ok.headers["cache-control"] == "no-store"

            # A new link revokes the previous one; racing creations leave exactly one.
            racing = await asyncio.gather(
                *(
                    http.post(f"{base}/vote-links", json={"profile_id": grandma})
                    for _ in range(4)
                )
            )
            assert {r.status_code for r in racing} == {201}
            tokens = [r.json()["token"] for r in racing]
            dead = await http.get(
                "/api/v1/vote/access", headers={"X-Access-Token": token_1}
            )
            assert dead.status_code == 404
            assert dead.headers["cache-control"] == "no-store"
            alive = [
                t
                for t in tokens
                if (
                    await http.get("/api/v1/vote/access", headers={"X-Access-Token": t})
                ).status_code
                == 200
            ]
            assert len(alive) == 1

            listing = (
                await http.get(f"{base}/vote-links", params={"state": "active"})
            ).json()
            assert listing["total"] == 1
            (link,) = listing["items"]
            assert link["profile_name"] == "Babcia"
            assert link["last_used_at"] is not None
            assert all(t not in str(listing) for t in [token_1, *tokens])
            revoked = (
                await http.get(f"{base}/vote-links", params={"state": "revoked"})
            ).json()
            assert revoked["total"] == 9
            by_profile = (
                await http.get(
                    f"{base}/vote-links",
                    params={"profile_id": grandma, "size": 2, "sort": "expires_at"},
                )
            ).json()
            assert (
                by_profile["total"],
                by_profile["pages"],
                len(by_profile["items"]),
            ) == (
                10,
                5,
                2,
            )

            # Votes: one through the link (author marker), one the host entered.
            async with get_sessionmaker()() as session:
                author = link_author(uuid.UUID(link["id"]))
                await feedback_db.upsert_rating(
                    session,
                    PlaceRating(
                        trip_id=uuid.UUID(trip_id),
                        profile_id=uuid.UUID(grandma),
                        place_id=museum,
                        value=RatingValue.DONT_WANT,
                        reason_code=ReasonCode.TOO_HARD_FOR_CHILD,
                        updated_by_sub=author,
                    ),
                )
                await feedback_db.insert_veto(
                    session,
                    PlaceVeto(
                        trip_id=uuid.UUID(trip_id),
                        profile_id=uuid.UUID(grandma),
                        place_id=museum,
                        created_by_sub=author,
                        on_behalf=False,
                    ),
                )
                await session.commit()
            await http.put(
                f"{base}/profiles/{grandma}/ratings/{park}", json={"value": "want"}
            )

            summary = (await http.get(f"{base}/vote-summary")).json()
            assert [i["place_name"] for i in summary["items"]] == ["Muzeum", "Park"]
            museum_row, park_row = summary["items"]
            assert (museum_row["dont_want"], museum_row["veto_count"]) == (1, 1)
            assert museum_row["votes"][0]["source"] == "link"
            assert museum_row["vetoes"][0]["source"] == "link"
            assert museum_row["vetoes"][0]["display_name"] == "Babcia"
            assert park_row["votes"][0]["source"] == "host"
            only = (
                await http.get(f"{base}/vote-summary", params={"source": "link"})
            ).json()
            assert [i["place_name"] for i in only["items"]] == ["Muzeum"]

            # Revoking kills the link at once; a foreign id is 404.
            deleted = await http.delete(f"{base}/vote-links/{link['id']}")
            assert deleted.status_code == 200
            assert deleted.json()["state"] == "revoked"
            gone = await http.get(
                "/api/v1/vote/access", headers={"X-Access-Token": alive[0]}
            )
            assert gone.status_code == 404
            missing = await http.delete(f"{base}/vote-links/{uuid.uuid4()}")
            assert missing.status_code == 404
        finally:
            await _cleanup([museum, park])
            await http.delete(base)


def test_vote_links_end_to_end() -> None:
    app = create_app()
    authorize(app, HOST)

    async def run() -> None:
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
        try:
            await _scenario(app)
        finally:
            await dispose_engine()

    asyncio.run(run())
