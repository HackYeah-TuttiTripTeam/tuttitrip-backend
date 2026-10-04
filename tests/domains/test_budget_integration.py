"""Daily budget and the proposal after an overrun on a real PostgreSQL (local smoke).

Run with `uv run pytest -m integration` after `alembic upgrade head`. The scenario
of backend#89: expenses over the budget of day 1, then a cheaper rest of the trip.
"""

import asyncio
import uuid

import httpx
import pytest
from fastapi import FastAPI

from tests.fixtures.personas import reference_family
from tests.fixtures.scenarios import FRIDAY, reference
from tests.shared.db_seed import add_person, seed_city, unseed_city
from tests.shared.fakes import authorize
from tuttitrip.main import create_app
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature

pytestmark = pytest.mark.integration

HOST = AuthenticatedUser(sub=f"auth0|budget-{uuid.uuid4()}")
MEMBER = AuthenticatedUser(sub=f"auth0|budget-member-{uuid.uuid4()}")


async def _spend(
    http: httpx.AsyncClient, base: str, everyone: list[str], amount: int, category: str
) -> None:
    # The first person pays for everybody, on Friday (day 1).
    sent = await http.post(
        f"{base}/expenses",
        json={
            "payer_profile_id": everyone[0],
            "amount": str(amount),
            "spent_on": FRIDAY.isoformat(),
            "category": category,
            "participants": [{"profile_id": p} for p in everyone],
        },
    )
    assert sent.status_code == 201, sent.text


async def _calm(http: httpx.AsyncClient, base: str) -> None:
    # No spending: every day has its share of the budget, nothing to propose.
    calm = (await http.get(f"{base}/budget/days")).json()
    assert [d["index"] for d in calm["days"]] == [1, 2, 3]
    first = calm["days"][0]
    assert first["spent"] == "0.00"
    assert first["remaining"] == first["budget_to"]
    assert calm["proposal"] is None
    idle = await http.post(f"{base}/budget/proposal")
    assert idle.status_code == 200
    assert idle.json()["skipped"] == "no_overrun"


async def _overrun(
    http: httpx.AsyncClient, base: str, ids: list[str], plan_id: str
) -> None:
    # Souvenirs do not take the budget of the attractions.
    await _spend(http, base, ids, 2000, "shopping")
    souvenirs = (await http.get(f"{base}/budget/days")).json()
    assert souvenirs["days"][0]["spent"] == "0.00"
    assert souvenirs["days"][0]["outside_plan"] == "2000.00"
    assert souvenirs["days"][0]["over_budget"] is False

    # Day 1 over its budget: a cheaper rest, with the cost and min r deltas.
    await _spend(http, base, ids, 1100, "food")
    over = (await http.get(f"{base}/budget/days")).json()
    assert over["days"][0]["over_budget"] is True
    assert float(over["days"][0]["remaining"]) < 0
    made = await http.post(f"{base}/budget/proposal")
    assert made.status_code == 201, made.text
    proposal = made.json()["proposal"]
    assert made.json()["created"] is True
    assert proposal["from_day"] == 2
    assert float(proposal["cost_delta"]) < 0
    assert float(proposal["cost"]) < float(proposal["previous_cost"])
    assert proposal["approval_status"] in {"not_needed", "pending"}
    assert 0 <= proposal["min_r"] <= 1

    stored = (await http.get(f"{base}/plans/{proposal['plan_id']}")).json()
    assert [d["index"] for d in stored["days"]] == [2, 3]
    latest = (await http.get(f"{base}/plans/latest")).json()
    assert latest["id"] == plan_id  # the plan itself did not change

    # The same expenses and data: the same proposal, nothing new stored.
    again = await http.post(f"{base}/budget/proposal")
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["proposal"] == proposal
    assert (await http.get(f"{base}/budget/days")).json()["proposal"] == proposal


async def _switched_off(http: httpx.AsyncClient, base: str) -> None:
    off = await http.patch(base, json={"propose_cheaper_alternatives": False})
    assert off.status_code == 200, off.text
    skipped = await http.post(f"{base}/budget/proposal")
    assert skipped.json()["skipped"] == "disabled"
    assert skipped.json()["proposal"] is None
    null = await http.patch(base, json={"propose_cheaper_alternatives": None})
    assert null.status_code == 422


async def _permissions(app: FastAPI, http: httpx.AsyncClient, base: str) -> None:
    authorize(app, MEMBER, (Grant(Feature.PLANNING_PLANS, Access.READ),))
    assert (await http.get(f"{base}/budget/days")).status_code == 404  # not on the trip
    authorize(app, HOST, (Grant(Feature.PLANNING_PLANS, Access.READ),))
    assert (await http.get(f"{base}/budget/days")).status_code == 200
    assert (await http.post(f"{base}/budget/proposal")).status_code == 403
    authorize(app, HOST, ())
    assert (await http.get(f"{base}/budget/days")).status_code == 403
    authorize(app, HOST)


async def _scenario(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        trip = await http.post(
            "/api/v1/trips",
            json=reference().trip.model_dump(mode="json", exclude_none=True),
        )
        assert trip.status_code == 201, trip.text
        assert trip.json()["propose_cheaper_alternatives"] is True
        base = f"/api/v1/trips/{trip.json()['id']}"
        try:
            ids = [
                await add_person(http, base, persona)
                for persona in reference_family().people
            ]
            plan = await http.post(f"{base}/plans")
            assert plan.status_code == 201, plan.text
            await _calm(http, base)
            await _overrun(http, base, ids, plan.json()["id"])
            await _switched_off(http, base)
            await _permissions(app, http, base)
        finally:
            await http.delete(base)


def test_budget_and_proposal_end_to_end() -> None:
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
