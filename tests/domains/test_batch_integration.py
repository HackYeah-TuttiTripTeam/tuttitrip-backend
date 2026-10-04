"""Candidates, justifications, trip linter and Takeout import on a real PostgreSQL.

Local smoke step (`uv run pytest -m integration` after `alembic upgrade head`),
not CI. The worker is a `FakeJobQueue` and a heartbeat row; the fixture city is
written to the database and removed at the end.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, func, select

from tests.fixtures.city import place_id, places
from tests.fixtures.personas import reference_family
from tests.fixtures.scenarios import reference
from tests.shared.db_seed import add_person, seed_city, unseed_city
from tests.shared.fakes import FakeJobQueue, authorize
from tuttitrip.main import create_app
from tuttitrip.planning.linter.models import PastedDocument
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker
from tuttitrip.shared.jobs.api import get_job_queue
from tuttitrip.shared.jobs.contracts import (
    CONTRACT_VERSION,
    FetchPlaceCandidatesInput,
    Workflow,
    WriteJustificationsInput,
)
from tuttitrip.shared.jobs.models import WorkerHeartbeat
from tuttitrip.shared.jobs.schemas import JobState

pytestmark = pytest.mark.integration

HOST = AuthenticatedUser(sub=f"auth0|batch-{uuid.uuid4()}")
WORKER_ID = f"batch-test-{uuid.uuid4()}"
NEW_CITY = "nowe-miasto"


async def _heartbeat(*, alive: bool) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            delete(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == WORKER_ID)
        )
        if alive:
            session.add(
                WorkerHeartbeat(
                    worker_id=WORKER_ID,
                    env=get_settings().environment,
                    contract_version=CONTRACT_VERSION,
                    min_contract_version=CONTRACT_VERSION,
                    app_version="test",
                    last_seen=datetime.now(UTC),
                )
            )
        await session.commit()


def _succeed(queue: FakeJobQueue, workflow_id: str, output: dict[str, Any]) -> None:
    job = queue.jobs[workflow_id]
    queue.jobs[workflow_id] = JobState(
        workflow_id=workflow_id,
        workflow_name=job.workflow_name,
        status="SUCCESS",
        output=output,
    )


def _plan_of(queue: FakeJobQueue, workflow_id: str) -> uuid.UUID:
    payload = queue.payloads[workflow_id]
    assert isinstance(payload, WriteJustificationsInput)
    return payload.plan_id


def _city_query(queue: FakeJobQueue, workflow_id: str) -> str | None:
    payload = queue.payloads[workflow_id]
    assert isinstance(payload, FetchPlaceCandidatesInput)
    return payload.city_query


def _ids(queue: FakeJobQueue, workflow: Workflow) -> list[str]:
    return [w for w, j in queue.jobs.items() if j.workflow_name == workflow.value]


async def _justifications(
    http: httpx.AsyncClient, base: str, queue: FakeJobQueue
) -> None:
    plans = f"{base}/plans"
    first = await http.post(plans)
    assert first.status_code == 201, first.text
    body = first.json()
    assert {v["justification_source"] for v in body["verdicts"]} == {"template"}
    assert all(v["justification"] for v in body["verdicts"])
    [job] = _ids(queue, Workflow.WRITE_JUSTIFICATIONS)
    assert _plan_of(queue, job) == uuid.UUID(body["id"])

    # The model's text replaces the template of that place only, and is kept.
    target = body["verdicts"][0]["place_id"]
    _succeed(
        queue,
        job,
        {
            "justifications": [
                {"place_id": target, "text": "Tekst modelu.", "source": "model"}
            ]
        },
    )
    latest = (await http.get(f"{plans}/latest")).json()
    by_place = {v["place_id"]: v for v in latest["verdicts"]}
    assert by_place[target]["justification"] == "Tekst modelu."
    assert by_place[target]["justification_source"] == "model"
    assert {v["justification_source"] for p, v in by_place.items() if p != target} == {
        "template"
    }
    del queue.jobs[job]  # the job system forgot it: the stored text stays
    kept = (await http.get(f"{plans}/{body['id']}")).json()
    assert {v["place_id"]: v["justification"] for v in kept["verdicts"]}[target] == (
        "Tekst modelu."
    )
    english = (await http.get(f"{plans}/latest", params={"locale": "en"})).json()
    assert english["verdicts"][0]["justification_source"] in {"model", "template"}

    # Without a worker the save works and the verdicts keep their templates.
    await _heartbeat(alive=False)
    chosen = [i["place_id"] for d in body["days"] for i in d["items"]]
    veto = await http.post(
        f"{base}/vetoes",
        json={
            "profile_id": body["fairness"]["per_person"][0]["profile_id"],
            "place_id": chosen[0],
        },
    )
    assert veto.status_code == 201, veto.text
    second = await http.post(plans)
    assert second.status_code == 201, second.text
    assert {v["justification_source"] for v in second.json()["verdicts"]} == {
        "template"
    }
    assert _ids(queue, Workflow.WRITE_JUSTIFICATIONS) == []
    await _heartbeat(alive=True)

    # Back with a worker, three quick recomputes keep only the newest job alive.
    for alpha in (0.5, 2, 0):
        assert (await http.post(plans, json={"alpha": alpha})).status_code == 201
    active = [
        w
        for w in _ids(queue, Workflow.WRITE_JUSTIFICATIONS)
        if queue.jobs[w].status != "CANCELLED"
    ]
    newest = (await http.get(f"{plans}/latest")).json()["id"]
    assert len(active) == 1
    assert _plan_of(queue, active[0]) == uuid.UUID(newest)

    await _trip_linter(http, base, queue, newest)


async def _trip_linter(  # ruff: ignore[too-many-locals] one story
    http: httpx.AsyncClient, base: str, queue: FakeJobQueue, plan_id: str
) -> None:
    linter = f"{base}/linter"
    ours = await http.post(f"{linter}/plans/{plan_id}")
    assert ours.status_code == 200, ours.text
    report = ours.json()
    assert report["count"] == 0
    assert {r["rule"] for r in report["results"]} >= {
        "budget",
        "unknown_place",
        "accessibility",
    }
    assert (await http.post(f"{linter}/plans/{uuid.uuid4()}")).status_code == 404

    museum, hevelianum = place_id("muzeum_miejskie"), place_id("hevelianum")
    text = "Dzień 1: 10:00 Muzeum 25 zł; 12:00 Jakaś knajpa; 14:00 Hevelianum"
    accepted = await http.post(f"{linter}/pastes", json={"text": text})
    assert accepted.status_code == 202, accepted.text
    paste_id, job = accepted.json()["paste_id"], accepted.json()["workflow_id"]
    pending = (await http.get(f"{linter}/pastes/{paste_id}")).json()
    assert pending["state"] == "pending"
    assert pending["report"] is None
    assert (
        await http.patch(
            f"{linter}/pastes/{paste_id}/items/1", json={"place_id": str(museum)}
        )
    ).status_code == 409

    parsed = {
        "items": [
            {
                "index": 0,
                "day": 1,
                "start_time": "10:00",
                "end_time": "11:00",
                "place_name": "Muzeum",
                "quote": "10:00 Muzeum 25 zł",
                "amount_minor": 2500,
            },
            {
                "index": 1,
                "day": 1,
                "start_time": "12:00",
                "place_name": "Jakaś knajpa",
                "quote": "12:00 Jakaś knajpa",
            },
            {
                "index": 2,
                "day": 1,
                "start_time": "14:00",
                "end_time": "15:00",
                "place_name": "Hevelianum",
                "quote": "14:00 Hevelianum",
            },
        ],
        "matches": [
            {"item_index": 0, "status": "matched", "place_id": str(museum)},
            {
                "item_index": 1,
                "status": "unrecognized",
                "candidates": [
                    {"place_id": str(museum), "name": "Muzeum Miejskie", "score": 0.3}
                ],
            },
            {
                "item_index": 2,
                "status": "needs_confirmation",
                "place_id": str(hevelianum),
                "candidates": [
                    {"place_id": str(hevelianum), "name": "Hevelianum", "score": 0.6}
                ],
            },
        ],
    }
    _succeed(queue, job, parsed)
    done = (await http.get(f"{linter}/pastes/{paste_id}")).json()
    assert done["state"] == "done"
    assert done["violations"] == done["report"]["count"] >= 2  # two unknown places
    assert [i["status"] for i in done["items"]] == [
        "matched",
        "unrecognized",
        "needs_confirmation",
    ]
    unknown = {
        f["place_name"]
        for r in done["report"]["results"]
        if r["rule"] == "unknown_place"
        for f in r["violations"]
    }
    assert unknown == {"Jakaś knajpa", "Hevelianum"}
    # Stored at the first read: another read gives the same report.
    assert (await http.get(f"{linter}/pastes/{paste_id}")).json() == done

    picked = await http.patch(
        f"{linter}/pastes/{paste_id}/items/2", json={"place_id": str(hevelianum)}
    )
    assert picked.status_code == 200, picked.text
    after = picked.json()
    item = after["items"][2]
    assert (item["status"], item["place_id"], item["chosen_by_host"]) == (
        "matched",
        str(hevelianum),
        True,
    )
    unknown_after = {
        f["place_name"]
        for r in after["report"]["results"]
        if r["rule"] == "unknown_place"
        for f in r["violations"]
    }
    assert unknown_after == {"Jakaś knajpa"}
    foreign = await http.patch(
        f"{linter}/pastes/{paste_id}/items/2", json={"place_id": str(uuid.uuid4())}
    )
    assert foreign.status_code == 422
    assert (await http.get(f"{linter}/pastes/{uuid.uuid4()}")).status_code == 404

    # No worker: 503, but the text is stored.
    await _heartbeat(alive=False)
    stored_before = await _documents()
    refused = await http.post(f"{linter}/pastes", json={"text": "Plan bez workera"})
    assert refused.status_code == 503
    assert await _documents() == stored_before + 1
    await _heartbeat(alive=True)


async def _documents() -> int:
    async with get_sessionmaker()() as session:
        return (
            await session.execute(select(func.count()).select_from(PastedDocument))
        ).scalar_one()


async def _candidates(
    http: httpx.AsyncClient, queue: FakeJobQueue, template: dict[str, object]
) -> None:
    # A demo city has places: nothing is started.
    trip = await http.post("/api/v1/trips", json=template)
    base = f"/api/v1/trips/{trip.json()['id']}"
    try:
        ready = await http.post(f"{base}/places/candidates")
        assert ready.status_code == 200
        assert ready.json()["state"] == "ready"
        assert (
            ready.json()["place_count"] == len(places()) - 0
            or ready.json()["place_count"] > 0
        )
        assert _ids(queue, Workflow.FETCH_PLACE_CANDIDATES) == []
    finally:
        await http.delete(base)

    new = await http.post("/api/v1/trips", json={**template, "city_slug": NEW_CITY})
    base = f"/api/v1/trips/{new.json()['id']}"
    try:
        await add_person(http, base, reference_family().people[0])
        # Planning a city without places answers 409 with the fetch job.
        plan = await http.post(f"{base}/plans")
        assert plan.status_code == 409, plan.text
        detail = plan.json()["detail"]
        assert detail["code"] == "catalog_missing"
        assert detail["city_slug"] == NEW_CITY
        job = detail["job_id"]
        assert job is not None
        assert _city_query(queue, job) == "nowe miasto"

        # Asking again, for the same city, is the same job (also from another trip).
        again = await http.post(f"{base}/places/candidates")
        assert again.status_code == 202
        assert again.json()["workflow_id"] == job
        wrong = await http.post(
            f"{base}/places/candidates", json={"city_query": "Gdańsk"}
        )
        assert wrong.status_code == 422

        running = await http.get(
            f"{base}/places/candidates/status", params={"job_id": job}
        )
        assert running.json()["state"] == "running"
        assert running.json()["place_count"] == 0
        foreign = await http.get(
            f"{base}/places/candidates/status",
            params={"job_id": "fetch_place_candidates-gdansk-1"},
        )
        assert foreign.status_code == 422

        queue.jobs[job] = JobState(
            workflow_id=job,
            workflow_name=Workflow.FETCH_PLACE_CANDIDATES.value,
            status="ERROR",
            error="Nie znaleziono takiego miasta",
            error_code="city_not_found",
        )
        failed = (
            await http.get(f"{base}/places/candidates/status", params={"job_id": job})
        ).json()
        assert (failed["state"], failed["error_code"]) == ("failed", "city_not_found")

        await _heartbeat(alive=False)
        assert (await http.post(f"{base}/places/candidates")).status_code == 503
        await _heartbeat(alive=True)
    finally:
        await http.delete(base)


async def _takeout(http: httpx.AsyncClient, base: str, profile_id: str) -> None:
    csv = (
        "Title,Note,URL,Tags,Comment\n"
        f"{places()['muzeum_miejskie'].name},,,,\n"
        "muzeum miejskie,,,,\n"
        "Park Oliwski,,,,\n"
        "Nieznane miejsce,,,,\n"
        ",,,,\n"
        ",,https://www.google.com/maps/place/Pizzeria/data=x,,\n"
    )
    url = f"{base}/places/import"
    response = await http.post(
        url,
        files={"file": ("Saved.csv", csv.encode(), "text/csv")},
        data={"profile_id": profile_id},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 5
    assert {m["place_name"] for m in body["matched"]} == {
        "Muzeum Miejskie",
        "Park Oliwski",
        "Pizzeria",
    }
    assert body["liked"] + body["kept"] == 3  # the persona rated some already
    assert {u["reason"] for u in body["unmatched"]} == {"duplicate", "not_in_catalog"}
    ratings = (await http.get(f"{base}/ratings")).json()
    liked = {
        r["place_id"]
        for r in ratings
        if r["profile_id"] == profile_id and r["value"] == "want"
    }
    assert str(place_id("muzeum_miejskie")) in liked

    # Again: the votes are already there, nothing changes.
    again = await http.post(
        url,
        files={"file": ("Saved.csv", csv.encode(), "text/csv")},
        data={"profile_id": profile_id},
    )
    assert (again.json()["liked"], again.json()["kept"]) == (0, 3)

    bad = await http.post(
        url,
        files={"file": ("x.csv", b"Name\nx\n", "text/csv")},
        data={"profile_id": profile_id},
    )
    assert bad.status_code == 422
    stranger = await http.post(
        url,
        files={"file": ("Saved.csv", csv.encode(), "text/csv")},
        data={"profile_id": str(uuid.uuid4())},
    )
    assert stranger.status_code == 404


async def _scenario(app: FastAPI, queue: FakeJobQueue) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        scenario = reference()
        template = scenario.trip.model_dump(mode="json", exclude_none=True)
        trip = await http.post("/api/v1/trips", json=template)
        assert trip.status_code == 201, trip.text
        base = f"/api/v1/trips/{trip.json()['id']}"
        try:
            ids = {}
            for persona in reference_family().people:
                ids[persona.key] = await add_person(http, base, persona)
            await _justifications(http, base, queue)
            await _takeout(http, base, next(iter(ids.values())))
        finally:
            await http.delete(base)
        await _candidates(http, queue, template)


def test_candidates_justifications_linter_and_takeout_end_to_end() -> None:
    app = create_app()
    authorize(app, HOST)
    queue = FakeJobQueue()
    app.dependency_overrides[get_job_queue] = lambda: queue

    async def run() -> None:
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
        try:
            await seed_city()
            await _heartbeat(alive=True)
            try:
                await _scenario(app, queue)
            finally:
                await _heartbeat(alive=False)
                await unseed_city()
        finally:
            await dispose_engine()

    asyncio.run(run())
