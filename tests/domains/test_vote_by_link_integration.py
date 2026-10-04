"""Voting through a link end to end on a real PostgreSQL (local smoke step, not CI).

Run with `uv run pytest -m integration` after `alembic upgrade head`. Uses the
city of the fixtures and the family of the plan scenario, like the plan test.
"""

import asyncio
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update

from tests.domains.test_plan_service_integration import _add_person, _seed, _unseed
from tests.fixtures.personas import reference_family
from tests.fixtures.scenarios import reference
from tests.shared.fakes import authorize
from tuttitrip.main import create_app
from tuttitrip.profiles.models import Profile
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker

pytestmark = pytest.mark.integration

HOST = AuthenticatedUser(sub=f"auth0|vote-{uuid.uuid4()}")
TOKEN_HEADER = "X-Access-Token"


def _planned(plan: dict[str, object]) -> list[str]:
    days = plan["days"]
    assert isinstance(days, list)
    return [i["place_id"] for d in days for i in d["items"]]


async def _scenario(app: FastAPI) -> None:  # ruff: ignore[too-many-locals, too-many-statements] - one story
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        trip = await http.post(
            "/api/v1/trips",
            json=reference().trip.model_dump(mode="json", exclude_none=True),
        )
        assert trip.status_code == 201, trip.text
        base = f"/api/v1/trips/{trip.json()['id']}"
        try:
            ids = {}
            for persona in reference_family().people:
                ids[persona.key] = await _add_person(http, base, persona)
            first = (await http.post(f"{base}/plans")).json()
            museum = _planned(first)[0]

            link = await http.post(
                f"{base}/vote-links", json={"profile_id": ids["babcia"]}
            )
            assert link.status_code == 201, link.text
            auth = {TOKEN_HEADER: link.json()["token"]}

            # Token routes ignore the logged-in user: the token is the credential.
            session = await http.get("/api/v1/vote/session", headers=auth)
            assert session.status_code == 200, session.text
            assert session.headers["cache-control"] == "no-store"
            assert session.json()["profile_name"] == "Babcia"
            shown = [p["place_id"] for p in session.json()["places"]]
            # The plan's places in order, then what she answered that it dropped.
            assert shown[: len(_planned(first))] == _planned(first)
            assert "preferences" not in session.text

            vote = f"/api/v1/vote/ratings/{shown[1]}"
            assert (
                await http.put(vote, headers=auth, json={"value": "dont_want"})
            ).status_code == 422
            rated = await http.put(
                vote,
                headers=auth,
                json={"value": "dont_want", "reason_code": "too_far"},
            )
            assert rated.status_code == 200, rated.text
            assert rated.json()["reason_code"] == "too_far"

            # The veto is stored once, with the link as author, and the plan moves on.
            vetoed = await http.post(
                "/api/v1/vote/vetoes", headers=auth, json={"place_id": museum}
            )
            assert vetoed.status_code == 200, vetoed.text
            again = await http.post(
                "/api/v1/vote/vetoes", headers=auth, json={"place_id": museum}
            )
            assert again.json()["veto_id"] == vetoed.json()["veto_id"]

            vetoes = (await http.get(f"{base}/vetoes")).json()
            assert [v["place_id"] for v in vetoes] == [museum]
            assert vetoes[0]["created_by_sub"].startswith("link:")
            latest = (await http.get(f"{base}/plans/latest")).json()
            assert latest["version"] == first["version"] + 1
            assert museum not in _planned(latest)
            summary = (await http.get(f"{base}/vote-summary")).json()
            assert {v["source"] for r in summary["items"] for v in r["vetoes"]} == {
                "link"
            }

            after = await http.get("/api/v1/vote/session", headers=auth)
            dropped = next(p for p in after.json()["places"] if p["place_id"] == museum)
            assert dropped["in_plan"] is False
            assert dropped["veto_id"] == vetoed.json()["veto_id"]

            # Taking the veto back brings the place into a new plan version.
            back = await http.delete(
                f"/api/v1/vote/vetoes/{vetoed.json()['veto_id']}", headers=auth
            )
            assert back.status_code == 200
            assert back.json()["veto_id"] is None
            restored = (await http.get(f"{base}/plans/latest")).json()
            assert restored["version"] == first["version"] + 2
            assert museum in _planned(restored)

            # A token of a profile cannot touch somebody else's veto.
            foreign = await http.delete(
                f"/api/v1/vote/vetoes/{uuid.uuid4()}", headers=auth
            )
            assert foreign.status_code == 404

            # Once the profile has an account the link is dead, like any bad token.
            async with get_sessionmaker()() as db:
                await db.execute(
                    update(Profile)
                    .where(Profile.id == uuid.UUID(ids["babcia"]))
                    .values(user_sub="auth0|babcia-took-over")
                )
                await db.commit()
            dead = await http.get("/api/v1/vote/session", headers=auth)
            assert dead.status_code == 404
            assert dead.headers["cache-control"] == "no-store"
            assert (await http.get("/api/v1/vote/session")).status_code == 401
        finally:
            await http.delete(base)


def test_voting_through_a_link_end_to_end() -> None:
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
