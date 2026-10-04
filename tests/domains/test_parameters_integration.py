"""Admin parameters on a real PostgreSQL: versions, plans and defaults (local, not CI).

Run with `uv run pytest -m integration` after `alembic upgrade head`.
"""

import asyncio
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete

from tests.fixtures.personas import reference_family
from tests.fixtures.scenarios import reference
from tests.shared.db_seed import add_person, seed_city, unseed_city
from tests.shared.fakes import authorize
from tuttitrip.main import create_app
from tuttitrip.planning.parameters.models import ParameterVersion
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature

pytestmark = pytest.mark.integration

ADMIN = AuthenticatedUser(sub=f"auth0|params-admin-{uuid.uuid4()}")
USER_GRANTS = (
    Grant(Feature.TRIPS, Access.WRITE),
    Grant(Feature.PROFILES, Access.WRITE),
    Grant(Feature.PLANNING_PLANS, Access.WRITE),
)
URL = "/api/v1/admin/planning/parameters"


async def _forget_versions() -> None:
    async with get_sessionmaker()() as session:
        await session.execute(delete(ParameterVersion))
        await session.commit()


async def _scenario(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        zero = (await http.get(URL)).json()
        assert zero["version"] == 0
        assert zero["values"]["strong_preference"] == pytest.approx(0.4)

        trip = await http.post(
            "/api/v1/trips",
            json=reference().trip.model_dump(mode="json", exclude_none=True),
        )
        base = f"/api/v1/trips/{trip.json()['id']}"
        try:
            for persona in reference_family().people:
                await add_person(http, base, persona)
            first = (await http.post(f"{base}/plans")).json()
            assert first["params"]["parameters_version"] == 0

            # A value outside the range is a 422 and stores nothing.
            bad = await http.post(URL, json={"values": {"alpha": 4}})
            assert bad.status_code == 422
            assert "4" not in str(bad.json())
            assert (await http.get(URL)).json()["version"] == 0

            # A new theta is a new version; the next plan records it.
            saved = await http.post(
                URL, json={"values": {"strong_preference": 0.5}, "note": "test"}
            )
            assert saved.status_code == 201, saved.text
            assert saved.json()["version"] == 1
            second = await http.post(f"{base}/plans")
            assert second.status_code == 201, second.text
            assert second.json()["params"]["parameters_version"] == 1
            assert second.json()["input_hash"] != first["input_hash"]

            # The stored plan keeps the version it was made with.
            old = (await http.get(f"{base}/plans/{first['id']}")).json()
            assert old["params"]["parameters_version"] == 0
            assert old == first

            # The administrator's alpha is the default of a new trip.
            await http.post(URL, json={"values": {"alpha": 2}})
            other = await http.post("/api/v1/trips", json={"name": "Nowy"})
            assert other.json()["fairness_alpha"] == pytest.approx(2)
            await http.delete(f"/api/v1/trips/{other.json()['id']}")

            versions = (await http.get(f"{URL}/versions")).json()
            assert versions["total"] == 2
            assert [v["version"] for v in versions["items"]] == [2, 1]

            # A regular user may not read or change them.
            authorize(app, AuthenticatedUser(sub="auth0|user"), USER_GRANTS)
            assert (await http.get(URL)).status_code == 403
            assert (await http.post(URL, json={})).status_code == 403
            authorize(app, ADMIN)
        finally:
            await http.delete(base)


def test_parameter_versions_end_to_end() -> None:
    app = create_app()
    authorize(app, ADMIN)

    async def run() -> None:
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
        try:
            await seed_city()
            await _forget_versions()
            try:
                await _scenario(app)
            finally:
                await _forget_versions()
                await unseed_city()
        finally:
            await dispose_engine()

    asyncio.run(run())
