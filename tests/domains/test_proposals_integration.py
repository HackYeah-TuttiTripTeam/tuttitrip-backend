"""Plan proposals, the ICS export, the budget consent and their notifications
on a real PostgreSQL.

Local smoke step (`pytest -m integration`), not CI.
"""

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from icalendar import Calendar
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError

from tests.fixtures.personas import reference_family
from tests.fixtures.scenarios import reference
from tests.shared.db_seed import add_person, seed_city, unseed_city
from tests.shared.fakes import authorize
from tuttitrip.main import create_app
from tuttitrip.notifications.models import Notification
from tuttitrip.planning.budget_approvals.models import BudgetApproval
from tuttitrip.planning.budget_approvals.services import approval_service
from tuttitrip.planning.overrides.models import PlanDecision
from tuttitrip.planning.plans import db as plans_db
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker
from tuttitrip.trips.models import TripMember
from tuttitrip.trips.schemas import TripRole

pytestmark = pytest.mark.integration

HOST = AuthenticatedUser(sub=f"auth0|pr-host-{uuid.uuid4()}")
CO_HOST = AuthenticatedUser(sub=f"auth0|pr-co-{uuid.uuid4()}")
MEMBER = AuthenticatedUser(sub=f"auth0|pr-member-{uuid.uuid4()}")
OUTSIDER = AuthenticatedUser(sub=f"auth0|pr-out-{uuid.uuid4()}")
ALL = (HOST, CO_HOST, MEMBER, OUTSIDER)


async def _new_trip(http: httpx.AsyncClient, **budget: object) -> str:
    body = reference().trip.model_dump(mode="json", exclude_none=True) | budget
    created = await http.post("/api/v1/trips", json=body)
    assert created.status_code == 201, created.text
    base = f"/api/v1/trips/{created.json()['id']}"
    for persona in reference_family().people:
        await add_person(http, base, persona)
    async with get_sessionmaker()() as session:
        trip_id = uuid.UUID(created.json()["id"])
        session.add_all(
            [
                TripMember(
                    trip_id=trip_id, user_sub=CO_HOST.sub, role=TripRole.CO_HOST
                ),
                TripMember(trip_id=trip_id, user_sub=MEMBER.sub, role=TripRole.MEMBER),
            ]
        )
        await session.commit()
    return base


async def _inbox(
    app: FastAPI, http: httpx.AsyncClient, user: AuthenticatedUser, kind: str
) -> list[dict[str, Any]]:
    authorize(app, user)
    page = await http.get("/api/v1/notifications", params={"size": 100, "type": kind})
    assert page.status_code == 200, page.text
    authorize(app, HOST)
    items: list[dict[str, Any]] = page.json()["items"]
    return items


async def _clean_notifications() -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            delete(Notification).where(Notification.user_sub.in_([u.sub for u in ALL]))
        )
        await session.commit()


async def _count(model: type[PlanDecision | BudgetApproval], trip_id: str) -> int:

    async with get_sessionmaker()() as session:
        result = await session.execute(
            select(func.count())
            .select_from(model)
            .where(model.trip_id == uuid.UUID(trip_id))
        )
        return result.scalar_one()


async def _proposals_story(  # ruff: ignore[too-many-statements, too-many-locals] one story
    app: FastAPI, http: httpx.AsyncClient
) -> None:
    base = await _new_trip(http)
    trip_id = base.rsplit("/", 1)[1]
    plans = f"{base}/plans"
    try:
        first = (await http.post(plans)).json()
        calendar = f"{plans}/{first['id']}/calendar.ics"

        # Nothing was approved yet: the file is not available.
        refused = await http.get(calendar)
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "plan.not_approved"
        assert (await http.get(f"{base}/proposals/current")).status_code == 404

        # Only the host sends; a co-host and a member get 403.
        for user in (CO_HOST, MEMBER):
            authorize(app, user)
            assert (await http.post(f"{base}/proposals")).status_code == 403
        authorize(app, HOST)
        sent = await http.post(f"{base}/proposals")
        assert sent.status_code == 201, sent.text
        proposal = sent.json()
        assert proposal["plan_id"] == first["id"]
        assert proposal["plan_hash"] == first["plan_hash"]
        assert proposal["status"] == "pending"
        assert proposal["tally"] == {
            "members": 3,
            "approvals": 1,  # the host proposes, which approves
            "rejections": 0,
            "comments": 0,
            "waiting": 2,
        }
        assert len(proposal["profiles_without_account"]) == len(
            reference_family().people
        )
        pid = proposal["id"]

        # Members with an account, except the host, are notified once, with actions.
        assert await _inbox(app, http, HOST, "proposal_waiting") == []
        for user in (CO_HOST, MEMBER):
            (note,) = await _inbox(app, http, user, "proposal_waiting")
            assert note["read_at"] is None
            assert note["params"] == {"proposal_id": pid, "plan_id": first["id"]}
            assert [a["code"] for a in note["actions"]] == [
                "approve_proposal",
                "open_plan",
            ]

        # A member approves: counts change, only their own notification is read.
        authorize(app, MEMBER)
        approved = await http.put(
            f"{base}/proposals/{pid}/response", json={"decision": "approve"}
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["tally"]["approvals"] == 2
        assert approved.json()["responses"][-1]["is_me"] is True
        (mine,) = await _inbox(app, http, MEMBER, "proposal_waiting")
        (theirs,) = await _inbox(app, http, CO_HOST, "proposal_waiting")
        assert mine["read_at"] is not None
        assert theirs["read_at"] is None
        # Nobody else is notified of an answer.
        assert await _inbox(app, http, HOST, "proposal_waiting") == []

        # A comment needs a remark; a rejection shows in the status with remarks.
        authorize(app, CO_HOST)
        url = f"{base}/proposals/{pid}/response"
        assert (await http.put(url, json={"decision": "comment"})).status_code == 422
        rejected = await http.put(
            url, json={"decision": "reject", "remark": "Za dużo chodzenia"}
        )
        body = rejected.json()
        assert body["status"] == "rejected"
        assert (body["tally"]["approvals"], body["tally"]["rejections"]) == (2, 1)
        assert any(r["remark"] == "Za dużo chodzenia" for r in body["responses"])

        # A person outside the trip sees neither.
        authorize(app, OUTSIDER)
        assert (await http.get(f"{base}/proposals/current")).status_code == 404
        assert (await http.get(calendar)).status_code == 404
        assert (await http.put(url, json={"decision": "approve"})).status_code == 404

        # The plan changes before the co-host answers again: the proposal is outdated,
        # answering is a 409 and the notifications of the old proposal are gone.
        authorize(app, HOST)
        victim = first["days"][0]["items"][0]["place_id"]
        assert (
            await http.post(
                f"{base}/overrides", json={"place_id": victim, "kind": "block"}
            )
        ).status_code == 201
        second = await http.post(plans)
        assert second.status_code == 201
        authorize(app, CO_HOST)
        current = (await http.get(f"{base}/proposals/current")).json()
        assert current["status"] == "outdated"
        stale = await http.put(url, json={"decision": "approve"})
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "proposal.outdated"
        assert stale.json()["detail"]["latest_plan_id"] == second.json()["id"]
        (gone,) = await _inbox(app, http, CO_HOST, "proposal_waiting")
        assert gone["read_at"] is not None

        # Sending the new version again; everybody approves, then the file is there.
        authorize(app, HOST)
        resent = await http.post(f"{base}/proposals")
        assert resent.status_code == 201
        assert resent.json()["plan_id"] == second.json()["id"]
        new_id = resent.json()["id"]
        assert (
            await http.post(f"{base}/proposals", json={"plan_id": first["id"]})
        ).status_code == 409
        for user in (MEMBER, CO_HOST):
            authorize(app, user)
            done = await http.put(
                f"{base}/proposals/{new_id}/response", json={"decision": "approve"}
            )
            assert done.status_code == 200, done.text
        assert done.json()["status"] == "approved"
        unread = [
            n
            for n in await _inbox(app, http, CO_HOST, "proposal_waiting")
            if n["read_at"] is None
        ]
        assert unread == []

        calendar = f"{plans}/{second.json()['id']}/calendar.ics"
        for user in (HOST, CO_HOST, MEMBER):
            authorize(app, user)
            file = await http.get(calendar)
            assert file.status_code == 200, file.text
            assert file.headers["content-type"].startswith("text/calendar")
            assert "attachment" in file.headers["content-disposition"]
            assert ".ics" in file.headers["content-disposition"]
        again = await http.get(calendar)
        assert again.content == file.content  # byte for byte
        events = list(Calendar.from_ical(file.content).walk("VEVENT"))
        stops = sum(len(d["items"]) for d in second.json()["days"])
        assert len(events) == stops > 0
        # The first version was never approved by everybody.
        assert (
            await http.get(f"{plans}/{first['id']}/calendar.ics")
        ).status_code == 409
        assert (
            await http.get(f"{plans}/{uuid.uuid4()}/calendar.ics")
        ).status_code == 404
    finally:
        authorize(app, HOST)
        await http.delete(base)
    assert await _count(PlanDecision, trip_id) == 0


BUDGETS = (("800", "1000"), ("700", "1100"), ("900", "1300"), ("600", "800"))


async def _trip_over_budget(http: httpx.AsyncClient) -> tuple[str, dict[str, Any]]:
    # A budget the family wants to exceed with a good reason (E6); the sweep keeps
    # the test independent of how the catalog prices lodging and tickets.
    for low, high in BUDGETS:
        base = await _new_trip(
            http, budget_total_min=low, budget_total_max=high, budget_flex_pct=50
        )
        plan = (await http.post(f"{base}/plans")).json()
        if plan["budget"]["needs_approval"]:
            return base, plan
        await http.delete(base)
    pytest.fail("no budget of the sweep produced a consent question")


async def _consent_story(  # ruff: ignore[too-many-statements, too-many-locals] one story
    app: FastAPI, http: httpx.AsyncClient
) -> None:
    base, flex = await _trip_over_budget(http)
    trip_id = base.rsplit("/", 1)[1]
    plans = f"{base}/plans"
    approvals = f"{base}/budget-approvals"
    try:
        assert flex["budget"]["needs_approval"] is True
        assert flex["budget"]["approval_status"] == "pending"
        page = (await http.get(approvals)).json()
        assert page["total"] == 1
        (question,) = page["items"]
        assert question["status"] == "pending"
        assert question["flex_plan_id"] == flex["id"]
        assert question["strict_plan_id"] == flex["budget"]["strict_plan_id"]
        assert float(question["over_budget"]) == pytest.approx(
            float(flex["budget"]["over_budget"])
        )
        assert float(question["kappa"]) == pytest.approx(float(flex["budget"]["kappa"]))
        assert question["gain_profile_name"] is not None
        approval_id = question["id"]

        # The host is notified, with the amount as a string and three actions.
        (note,) = await _inbox(app, http, HOST, "budget_approval_waiting")
        assert note["read_at"] is None
        assert note["params"]["approval_id"] == approval_id
        assert note["params"]["over_budget"] == question["over_budget"]
        assert note["params"]["kappa"] == question["kappa"]
        assert [a["code"] for a in note["actions"]] == [
            "approve_budget",
            "reject_budget",
            "open_plan",
        ]
        assert await _inbox(app, http, CO_HOST, "budget_approval_waiting") == []

        # A co-host cannot decide (403), a member can read the question.
        for user in (CO_HOST, MEMBER):
            authorize(app, user)
            assert (
                await http.post(f"{approvals}/{approval_id}/approve")
            ).status_code == 403
            assert (
                await http.post(f"{approvals}/{approval_id}/reject")
            ).status_code == 403
            assert (await http.get(approvals)).status_code == 200
        authorize(app, OUTSIDER)
        assert (await http.get(approvals)).status_code == 404
        authorize(app, HOST)
        assert (
            await http.post(f"{approvals}/{uuid.uuid4()}/approve")
        ).status_code == 404

        # Approval keeps P_flex; the log has the amount, kappa, reason and author.
        decided = await http.post(
            f"{approvals}/{approval_id}/approve", json={"reason": "Warto"}
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "approved"
        assert decided.json()["active_plan_id"] == flex["id"]
        assert decided.json()["decided_by_sub"] == HOST.sub
        latest = (await http.get(f"{plans}/latest")).json()
        assert latest["id"] == flex["id"]
        assert latest["budget"]["approval_status"] == "approved"
        log = (
            await http.get(f"{base}/decisions", params={"kind": "budget_approval"})
        ).json()
        assert log["total"] == 1
        entry = log["items"][0]
        assert entry["created_by_sub"] == HOST.sub
        assert entry["reason"] == "Warto"
        consent = entry["effects"]["budget"]
        assert consent["outcome"] == "approved"
        assert consent["approval_id"] == approval_id
        assert float(consent["over_budget"]) == pytest.approx(
            float(question["over_budget"])
        )
        assert float(consent["kappa"]) == pytest.approx(float(question["kappa"]))
        assert consent["gain_profile_id"] == question["gain_profile_id"]
        assert consent["gain_points"] == pytest.approx(question["gain_points"])
        assert float(entry["effects"]["d_cost"]) > 0
        (read,) = await _inbox(app, http, HOST, "budget_approval_waiting")
        assert read["read_at"] is not None
        # Decided once.
        again = await http.post(f"{approvals}/{approval_id}/reject")
        assert again.status_code == 409
        assert again.json()["detail"]["code"] == "budget_approval.not_pending"
        assert again.json()["detail"]["status"] == "approved"
        only = await http.get(approvals, params={"status": "pending"})
        assert only.json()["total"] == 0
        await _decided_row_is_final(approval_id)
    finally:
        await http.delete(base)
    assert await _count(BudgetApproval, trip_id) == 0


async def _decided_row_is_final(approval_id: str) -> None:
    async with get_sessionmaker()() as session:
        with pytest.raises(DBAPIError, match="does not change"):
            await session.execute(
                text("UPDATE budget_approvals SET kappa = 1 WHERE id = :i"),
                {"i": approval_id},
            )
        await session.rollback()


async def _reject_and_supersede_story(  # ruff: ignore[too-many-locals] one story
    app: FastAPI, http: httpx.AsyncClient
) -> None:
    base, flex = await _trip_over_budget(http)
    plans = f"{base}/plans"
    approvals = f"{base}/budget-approvals"
    try:
        first = (await http.get(approvals)).json()["items"][0]

        # A new plan version supersedes the open question and clears its notification.
        victim = flex["days"][0]["items"][0]["place_id"]
        await http.post(f"{base}/overrides", json={"place_id": victim, "kind": "block"})
        recomputed = (await http.post(plans)).json()
        listed = (
            await http.get(approvals, params={"sort": "created_at", "dir": "asc"})
        ).json()
        statuses = {i["id"]: i["status"] for i in listed["items"]}
        assert statuses[first["id"]] == "superseded"
        (stale,) = (
            n
            for n in await _inbox(app, http, HOST, "budget_approval_waiting")
            if n["params"]["approval_id"] == first["id"]
        )
        assert stale["read_at"] is not None
        gone = await http.post(f"{approvals}/{first['id']}/approve")
        assert gone.status_code == 409
        assert gone.json()["detail"]["status"] == "superseded"
        assert recomputed["budget"]["needs_approval"] is True
        pending = next(i for i in listed["items"] if i["status"] == "pending")
        assert pending["flex_plan_id"] == recomputed["id"]

        # Rejecting activates P_strict, within B_do, as the newest version.
        rejected = await http.post(f"{approvals}/{pending['id']}/reject")
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        active = (await http.get(f"{plans}/latest")).json()
        assert active["id"] == rejected.json()["active_plan_id"]
        assert active["version"] == recomputed["version"] + 1
        assert active["budget"]["needs_approval"] is False
        assert float(active["budget"]["cost"]) <= float(active["budget"]["b_to"])
        same = await http.post(plans)  # the input did not change: the choice holds
        assert same.status_code == 200
        assert same.json()["id"] == active["id"]
        log = (
            await http.get(f"{base}/decisions", params={"kind": "budget_approval"})
        ).json()
        assert log["items"][0]["effects"]["budget"]["outcome"] == "rejected"
    finally:
        await http.delete(base)


async def _rollback_story(http: httpx.AsyncClient) -> None:
    base, flex = await _trip_over_budget(http)
    trip_id = uuid.UUID(base.rsplit("/", 1)[1])
    try:
        async with get_sessionmaker()() as session:
            latest = await plans_db.select_latest(session, trip_id)
            strict = await plans_db.select_by_id(
                session, trip_id, uuid.UUID(flex["budget"]["strict_plan_id"])
            )
            assert latest is not None
            assert strict is not None
            await approval_service.on_plan_changed(session, trip_id)
            await session.commit()
            await _clean_notifications()
            row = await approval_service.open_for_plan(session, latest, strict)
            assert row is not None
            await session.rollback()  # the plan side failed after the question was made
        async with get_sessionmaker()() as session:
            notes = await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_sub == HOST.sub)
            )
            assert notes == 0
    finally:
        await http.delete(base)


def test_proposals_ics_consent_and_notifications_end_to_end() -> None:
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
                    await _proposals_story(app, http)
                    await _consent_story(app, http)
                    await _reject_and_supersede_story(app, http)
                    await _rollback_story(http)
            finally:
                await _clean_notifications()
                await unseed_city()
        finally:
            await dispose_engine()

    asyncio.run(run())
