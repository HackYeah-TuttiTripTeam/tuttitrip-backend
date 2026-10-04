"""MCP tool services on a real PostgreSQL (local, not CI).

Run with `uv run pytest -m integration` after `alembic upgrade head`. The tools'
bodies are the service functions; FastMCP's own plumbing is covered by test_mcp.
"""

import asyncio
import uuid

import httpx
import pytest

from tests.fixtures.personas import reference_family
from tests.fixtures.scenarios import reference
from tests.shared.db_seed import add_person, seed_city, unseed_city
from tests.shared.fakes import authorize
from tuttitrip.main import create_app
from tuttitrip.mcp import schemas as constants
from tuttitrip.mcp.services import tool_service
from tuttitrip.mcp.services.tool_service import ToolFailedError
from tuttitrip.planning.linter.schemas import NamedPlan
from tuttitrip.profiles.feedback.schemas import RatingValue
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker

pytestmark = pytest.mark.integration

HOST = AuthenticatedUser(sub=f"auth0|mcp-host-{uuid.uuid4()}")
STRANGER = AuthenticatedUser(sub=f"auth0|mcp-stranger-{uuid.uuid4()}")
CHATBOT = NamedPlan.model_validate(
    {
        "days": [
            {
                "day": "2026-10-09",
                "items": [
                    {"name": "Park Oliwski", "start": "09:00", "end": "10:00"},
                    {
                        "name": "Nie ma takiego miejsca",
                        "start": "10:05",
                        "end": "11:00",
                    },
                ],
            }
        ]
    }
)


async def _tools(trip_id: uuid.UUID, chosen: str, profile: str) -> None:
    async with get_sessionmaker()() as session:
        plan = await tool_service.get_plan(session, HOST, trip_id, None)
        assert plan.day_count == len(plan.days) == 3
        first = await tool_service.get_plan(session, HOST, trip_id, 1)
        assert [d.index for d in first.days] == [1]
        ledger = await tool_service.get_fairness(session, HOST, trip_id)
        assert ledger.plan_hash == plan.plan_hash
        assert ledger.fairness.group_size == len(ledger.fairness.per_person) > 1
        assert all(len(p.domains) == 5 for p in ledger.fairness.per_person)

        report = await tool_service.get_violations(session, HOST, trip_id)
        assert {r.rule for r in report.results} >= {"unknown_place", "opening_hours"}

        linted = await tool_service.lint_plan(session, HOST, trip_id, CHATBOT)
        unknown = next(r for r in linted.results if r.rule == "unknown_place")
        assert unknown.count == 1  # only the invented place

        # A stranger learns nothing, not even that the trip exists.
        for call in (
            tool_service.get_plan(session, STRANGER, trip_id, None),
            tool_service.get_fairness(session, STRANGER, trip_id),
            tool_service.get_violations(session, STRANGER, trip_id),
        ):
            with pytest.raises(ToolFailedError, match=constants.TRIP_NOT_FOUND):
                await call

        rating = await tool_service.rate_place(
            session, HOST, trip_id, uuid.UUID(chosen), RatingValue.WANT, None
        )
        assert rating.value is RatingValue.WANT
        again = await tool_service.rate_place(
            session, HOST, trip_id, uuid.UUID(chosen), RatingValue.WANT, None
        )
        assert again.profile_id == rating.profile_id

        result = await tool_service.veto_place(
            session, HOST, trip_id, uuid.UUID(chosen), uuid.UUID(profile)
        )
        assert result.veto.on_behalf is True  # the host acts for another profile
        assert result.plan_version == plan.version + 1
        assert result.removed  # the place left the plan
        with pytest.raises(ToolFailedError, match=constants.VETO_EXISTS):
            await tool_service.veto_place(
                session, HOST, trip_id, uuid.UUID(chosen), uuid.UUID(profile)
            )


async def _scenario(app_client: httpx.AsyncClient) -> None:
    trip = await app_client.post(
        "/api/v1/trips",
        json=reference().trip.model_dump(mode="json", exclude_none=True),
    )
    base = f"/api/v1/trips/{trip.json()['id']}"
    try:
        ids = [await add_person(app_client, base, p) for p in reference_family().people]
        plan = await app_client.post(f"{base}/plans")
        assert plan.status_code == 201, plan.text
        chosen = plan.json()["days"][0]["items"][0]["place_id"]
        await _tools(uuid.UUID(trip.json()["id"]), chosen, ids[-1])
    finally:
        await app_client.delete(base)


def test_tool_services_end_to_end() -> None:
    app = create_app()
    authorize(app, HOST)
    tool_service.write_limiter.cache_clear()

    async def run() -> None:
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
        try:
            await seed_city()
            try:
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://t"
                ) as http:
                    await _scenario(http)
            finally:
                await unseed_city()
        finally:
            await dispose_engine()

    asyncio.run(run())
