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
from sqlalchemy import func, select

from tests.fixtures.city import place_id
from tests.fixtures.personas import reference_family
from tests.fixtures.scenarios import reference
from tests.shared.db_seed import add_person, seed_city, unseed_city
from tests.shared.fakes import authorize
from tuttitrip.main import create_app
from tuttitrip.planning.plans.models import PlanVersion
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.models import TripMember
from tuttitrip.trips.schemas import TripRole

pytestmark = pytest.mark.integration

HOST = AuthenticatedUser(sub=f"auth0|plan-{uuid.uuid4()}")
MEMBER = AuthenticatedUser(sub=f"auth0|plan-member-{uuid.uuid4()}")


async def _privacy_and_permissions(
    app: FastAPI,
    http: httpx.AsyncClient,
    base: str,
    trip_id: str,
    ids: dict[str, str],
) -> None:
    plans = f"{base}/plans"
    host_view = (await http.get(f"{plans}/latest")).json()
    assert {e["profile_id"] for e in host_view["explain"]} >= set(ids.values())

    # A plain member: the ledger is visible, `explain` only for their own profile.
    async with get_sessionmaker()() as session:
        session.add(
            TripMember(
                trip_id=uuid.UUID(trip_id), user_sub=MEMBER.sub, role=TripRole.MEMBER
            )
        )
        await session.commit()
    own = await http.post(
        f"{base}/profiles",
        json={"display_name": "Gość", "age": 30, "user_sub": MEMBER.sub},
    )
    assert own.status_code == 201, own.text
    authorize(app, MEMBER)
    seen = (await http.get(f"{plans}/latest")).json()
    assert seen["fairness"]["per_person"] == host_view["fairness"]["per_person"]
    assert {e["profile_id"] for e in seen["explain"]} <= {own.json()["id"]}
    # Any member may ask for a plan; the new profile is new input, hence a new version.
    asked = await http.post(plans)
    assert asked.status_code == 201, asked.text
    assert {e["profile_id"] for e in asked.json()["explain"]} <= {own.json()["id"]}
    again = await http.post(plans)
    assert again.status_code == 200

    # Without the WRITE permission POST is 403, reading still works.
    authorize(app, MEMBER, (Grant(Feature.PLANNING_PLANS, Access.READ),))
    assert (await http.post(plans)).status_code == 403
    assert (await http.get(f"{plans}/latest")).status_code == 200
    authorize(app, HOST)


async def _solo_trip(http: httpx.AsyncClient) -> None:
    scenario = reference()
    created = await http.post(
        "/api/v1/trips",
        json=scenario.trip.model_dump(mode="json", exclude_none=True),
    )
    base = f"/api/v1/trips/{created.json()['id']}"
    try:
        solo = await http.post(f"{base}/plans")
        assert solo.status_code == 201, solo.text
        body = solo.json()
        assert body["fairness"]["group_size"] == 1
        assert body["fairness"]["jain"] == pytest.approx(1)
        assert body["fairness"]["per_person"][0]["r"] == pytest.approx(1)
        assert body["telemetry"]["solo_runs"] == 0
    finally:
        await http.delete(base)


async def _plans_of(trip_id: str, *, alternatives: bool = False) -> int:
    async with get_sessionmaker()() as session:
        stmt = (
            select(func.count())
            .select_from(PlanVersion)
            .where(PlanVersion.trip_id == uuid.UUID(trip_id))
        )
        if not alternatives:
            stmt = stmt.where(PlanVersion.alternative_of.is_(None))
        return (await session.execute(stmt)).scalar_one()


async def _scenario(app: FastAPI) -> None:  # ruff: ignore[too-many-locals, too-many-statements] - one story
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
                ids[persona.key] = await add_person(http, base, persona)

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

            # One lodging base for both nights, a search area, and transit tickets
            # shown for information (never in the cost).
            lodging = body["lodging"]
            assert lodging["nights"] == 2
            assert lodging["place_id"] in {
                str(place_id(k))
                for k in ("apartament_basen", "hotel_centrum", "hostel_dworzec")
            }
            assert lodging["exceptional"] == []
            assert lodging["search_area"]["radius_m"] >= 500
            transit = body["transit"]
            assert transit["verified"] is True
            assert float(transit["total"]) > 0
            assert len(transit["rides_per_day"]) == 3
            assert float(body["budget"]["cost"]) >= float(lodging["cost_total"])

            again = await http.post(plans)
            assert again.status_code == 200
            assert again.json() == body

            # Rain: replan the last day from the morning; nothing is stored.
            stored = await http.get(f"{plans}/latest")
            rain = await http.post(
                f"{plans}/{body['id']}/replan",
                json={
                    "context": "rain",
                    "day": 3,
                    "as_of": f"{body['days'][2]['date']}T08:00:00+02:00",
                },
            )
            assert rain.status_code == 200, rain.text
            assert rain.json()["status"] == "active"  # the host replans
            assert rain.json()["elapsed_ms"] < 2000
            assert rain.json()["stops"]
            assert (await http.get(f"{plans}/latest")).json() == stored.json()
            assert (
                await http.post(
                    f"{plans}/{body['id']}/replan",
                    json={"day": 9, "as_of": "2026-10-11T08:00:00+02:00"},
                )
            ).status_code == 422

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

            # Taking the veto back reverts the input to an OLDER state: that is a
            # new version (not the old one), and "latest" is the plan of the input.
            veto_id = veto.json()["id"]
            assert (await http.delete(f"{base}/vetoes/{veto_id}")).status_code == 204
            reverted = await http.post(plans)
            assert reverted.status_code == 201
            assert reverted.json()["version"] == 3
            assert reverted.json()["input_hash"] == body["input_hash"]
            assert reverted.json()["plan_hash"] == body["plan_hash"]
            assert (await http.get(f"{plans}/latest")).json() == reverted.json()

            # The alpha slider is part of the input; omitted means the trip's own.
            other = await http.post(plans, json={"alpha": 2})
            assert other.status_code == 201
            assert other.json()["params"]["alpha"] == 2
            assert other.json()["version"] == 4

            # The preset is recorded (it has no effect on the weights yet).
            preset = await http.post(plans, json={"weight_preset": "equal"})
            assert preset.status_code == 201
            assert preset.json()["params"]["weight_preset"] == "equal"
            assert preset.json()["params"]["alpha"] == pytest.approx(1.0)
            assert preset.json()["version"] == 5

            await _privacy_and_permissions(app, http, base, trip.json()["id"], ids)

            # Racing requests for an input nobody stored yet never give a 500.
            racing = await asyncio.gather(
                *(http.post(plans, json={"alpha": 0.5}) for _ in range(4))
            )
            assert {r.status_code for r in racing} <= {200, 201}
            assert sum(r.status_code == 201 for r in racing) == 1
            assert len({r.json()["id"] for r in racing}) == 1
            assert (await http.get(f"{plans}/latest")).json()["version"] == 7
            trip_id = trip.json()["id"]
            assert await _plans_of(trip_id) == 7
            await _solo_trip(http)
        finally:
            await http.delete(base)
        # The versions go with the trip (ON DELETE CASCADE).
        assert await _plans_of(trip.json()["id"], alternatives=True) == 0


def test_plan_versions_end_to_end() -> None:
    app = create_app()
    authorize(app, HOST)

    async def run() -> None:
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
        try:
            await seed_city()
            try:
                await _scenario(app)
            finally:
                await unseed_city()
        finally:
            await dispose_engine()

    asyncio.run(run())
