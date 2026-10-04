"""Host overrides, the decision log and the budget consent on a real PostgreSQL.

Local smoke step (`pytest -m integration`), not CI.
"""

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from tests.fixtures.city import place_id
from tests.fixtures.personas import reference_family
from tests.fixtures.scenarios import reference
from tests.shared.db_seed import add_person, seed_city, unseed_city
from tests.shared.fakes import authorize
from tuttitrip.main import create_app
from tuttitrip.planning.overrides.models import PlanDecision
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker
from tuttitrip.trips.models import TripMember
from tuttitrip.trips.schemas import TripRole

pytestmark = pytest.mark.integration

HOST = AuthenticatedUser(sub=f"auth0|ov-host-{uuid.uuid4()}")
CO_HOST = AuthenticatedUser(sub=f"auth0|ov-co-{uuid.uuid4()}")
MEMBER = AuthenticatedUser(sub=f"auth0|ov-member-{uuid.uuid4()}")


async def _new_trip(
    http: httpx.AsyncClient, **budget: object
) -> tuple[str, dict[str, str]]:
    body = reference().trip.model_dump(mode="json", exclude_none=True) | budget
    created = await http.post("/api/v1/trips", json=body)
    assert created.status_code == 201, created.text
    base = f"/api/v1/trips/{created.json()['id']}"
    ids = {}
    for persona in reference_family().people:
        ids[persona.key] = await add_person(http, base, persona)
    return base, ids


async def _decision_count(trip_id: str) -> int:
    async with get_sessionmaker()() as session:
        rows = await session.execute(
            select(func.count())
            .select_from(PlanDecision)
            .where(PlanDecision.trip_id == uuid.UUID(trip_id))
        )
        return rows.scalar_one()


async def _log_is_append_only(trip_id: str) -> None:
    async with get_sessionmaker()() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(
                text("UPDATE plan_decisions SET reason = 'x' WHERE trip_id = :t"),
                {"t": trip_id},
            )
        await session.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(
                text("DELETE FROM plan_decisions WHERE trip_id = :t"), {"t": trip_id}
            )
        await session.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text("TRUNCATE plan_decisions"))
        await session.rollback()


async def _overrides_story(  # ruff: ignore[too-many-statements, too-many-locals] one story
    app: FastAPI, http: httpx.AsyncClient
) -> None:
    base, ids = await _new_trip(http)
    trip_id = base.rsplit("/", 1)[1]
    plans = f"{base}/plans"
    try:
        first = (await http.post(plans)).json()
        chosen = [i["place_id"] for d in first["days"] for i in d["items"]]
        victim = chosen[0]
        assert first["verdicts"] is not None
        verdicts = {v["place_id"]: v for v in first["verdicts"]}
        assert all(verdicts[p]["verdict"] in {"fits", "must"} for p in chosen)
        assert verdicts[str(place_id("restauracja_morska"))]["verdict"] == "skip"
        assert verdicts[str(place_id("restauracja_morska"))]["skip_codes"] == ["veto"]

        # Preview stores nothing and the plan does not change.
        preview = await http.post(
            f"{base}/overrides/preview", json={"place_id": victim, "kind": "block"}
        )
        assert preview.status_code == 200, preview.text
        effects = preview.json()["effects"]
        assert {"d_min_r", "d_jain", "d_r", "d_cost", "d_minutes"} <= set(effects)
        assert len(effects["d_r"]) == len(ids) + 1  # the family and the host
        assert (await http.get(f"{plans}/latest")).json() == first
        assert (await http.get(f"{base}/decisions")).json()["total"] == 0

        # Saved: the log has the same numbers, the author and the reason.
        saved = await http.post(
            f"{base}/overrides",
            json={"place_id": victim, "kind": "block", "reason": "zamknięte"},
        )
        assert saved.status_code == 201, saved.text
        assert saved.json()["effects"] == effects
        log = (await http.get(f"{base}/decisions")).json()
        assert log["total"] == 1
        entry = log["items"][0]
        assert entry["effects"] == effects
        assert entry["reason"] == "zamknięte"
        assert entry["created_by_sub"] == HOST.sub
        assert entry["kind"] == "block"

        # A blocked place is not in the next plan.
        after = await http.post(plans)
        assert after.status_code == 201
        assert after.json()["version"] == 2
        assert victim not in [
            i["place_id"] for d in after.json()["days"] for i in d["items"]
        ]
        verdict = {v["place_id"]: v for v in after.json()["verdicts"]}[victim]
        assert verdict["verdict"] == "skip"
        assert "blocked" in verdict["skip_codes"]

        # A "must" that runs into a veto is a 409 with the conflict.
        sea = str(place_id("restauracja_morska"))
        for url in ("overrides/preview", "overrides"):
            clash = await http.post(
                f"{base}/{url}", json={"place_id": sea, "kind": "must"}
            )
            assert clash.status_code == 409, clash.text
            conflict = clash.json()["conflicts"][0]
            assert conflict["reason_code"] == "veto_blocks_place"
            assert conflict["place_id"] == sea
            assert conflict["profile_ids"] == [ids["babcia"]]

        # A "must" that is allowed ends up in the plan.
        must = chosen[1]
        forced = await http.post(
            f"{base}/overrides",
            json={"place_id": must, "kind": "must", "reason": "dzieci"},
        )
        assert forced.status_code == 201, forced.text
        third = (await http.post(plans)).json()
        assert must in [i["place_id"] for d in third["days"] for i in d["items"]]

        # Only the host decides; a co-host gets 403, a member may read the log.
        async with get_sessionmaker()() as session:
            session.add_all(
                [
                    TripMember(
                        trip_id=uuid.UUID(trip_id),
                        user_sub=CO_HOST.sub,
                        role=TripRole.CO_HOST,
                    ),
                    TripMember(
                        trip_id=uuid.UUID(trip_id),
                        user_sub=MEMBER.sub,
                        role=TripRole.MEMBER,
                    ),
                ]
            )
            await session.commit()
        authorize(app, CO_HOST)
        body = {"place_id": victim, "kind": "block"}
        assert (await http.post(f"{base}/overrides", json=body)).status_code == 403
        assert (
            await http.post(f"{base}/overrides/preview", json=body)
        ).status_code == 403
        authorize(app, MEMBER)
        assert (await http.get(f"{base}/decisions")).status_code == 200
        assert (await http.post(f"{base}/overrides", json=body)).status_code == 403
        authorize(app, HOST)

        # Revoking takes the block back and is logged with its own numbers.
        revoked = await http.delete(f"{base}/overrides/{saved.json()['id']}")
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["revoked_at"] is not None
        kinds = [
            e["kind"] for e in (await http.get(f"{base}/decisions")).json()["items"]
        ]
        assert kinds == ["revoke", "must", "block"]
        only_block = (
            await http.get(f"{base}/decisions", params={"kind": "block"})
        ).json()
        assert only_block["total"] == 1
        assert (
            await http.delete(f"{base}/overrides/{uuid.uuid4()}")
        ).status_code == 404
        back = (await http.post(plans)).json()
        assert back["version"] == 4

        assert await _decision_count(trip_id) == 3
        await _log_is_append_only(trip_id)
    finally:
        await http.delete(base)
    assert await _decision_count(trip_id) == 0  # the log goes with the trip


BUDGETS = (("800", "1000"), ("600", "800"), ("900", "1100"), ("1000", "1300"))


async def _consent_story(http: httpx.AsyncClient) -> None:
    # Some budget makes the family want more than B_do with a good reason (E6).
    for low, high in BUDGETS:
        base, _ = await _new_trip(
            http, budget_total_min=low, budget_total_max=high, budget_flex_pct=50
        )
        plans = f"{base}/plans"
        try:
            created = await http.post(plans)
            assert created.status_code == 201, created.text
            budget = created.json()["budget"]
            assert float(budget["cost"]) <= float(budget["b_max"])
            if not budget["needs_approval"]:
                assert budget["kappa"] is None
                continue
            await _check_consent(http, plans, created.json())
            return
        finally:
            await http.delete(base)
    pytest.fail("no budget of the sweep produced a consent question")


async def _check_consent(
    http: httpx.AsyncClient, plans: str, plan: dict[str, Any]
) -> None:
    budget = plan["budget"]
    assert budget["unlimited"] is False
    assert budget["approval_status"] == "pending"
    assert float(budget["kappa"]) > 0
    assert float(budget["gain_points"]) > 0
    assert budget["gain_profile_id"] is not None
    assert float(budget["b_to"]) < float(budget["cost"]) <= float(budget["b_max"])
    assert float(budget["over_budget"]) == pytest.approx(
        float(budget["cost"]) - float(budget["b_to"])
    )
    # P_strict is stored as the alternative of the same version.
    strict = await http.get(f"{plans}/{budget['strict_plan_id']}")
    assert strict.status_code == 200
    assert strict.json()["version"] == plan["version"]
    assert float(strict.json()["budget"]["cost"]) <= float(budget["b_to"])
    assert float(strict.json()["budget"]["cost"]) == pytest.approx(
        float(budget["strict_cost"])
    )
    assert strict.json()["budget"]["needs_approval"] is False
    # "Latest" is the plan awaiting consent, never its alternative.
    assert (await http.get(f"{plans}/latest")).json()["id"] == plan["id"]
    again = await http.post(plans)
    assert again.status_code == 200
    assert again.json()["id"] == plan["id"]


def test_overrides_log_and_consent_end_to_end() -> None:
    app = create_app()
    authorize(app, HOST)

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
                    await _overrides_story(app, http)
                    await _consent_story(http)
            finally:
                await unseed_city()
        finally:
            await dispose_engine()

    asyncio.run(run())
