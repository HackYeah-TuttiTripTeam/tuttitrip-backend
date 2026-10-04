"""Google Calendar and Drive export and the "anyway" suggestion on a real PostgreSQL.

Google is a stub server behind an httpx mock transport; the token comes from a fake.
Local smoke step (`pytest -m integration`), not CI.
"""

import asyncio
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any, override

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from tests.domains.test_proposals_integration import (
    CO_HOST,
    HOST,
    MEMBER,
    OUTSIDER,
    _new_trip,
)
from tests.shared.db_seed import seed_city, unseed_city
from tests.shared.fakes import FakeJobQueue, authorize
from tuttitrip.main import create_app
from tuttitrip.planning.anyway.models import AnywayState
from tuttitrip.planning.plans.models import PlanVersion
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker
from tuttitrip.shared.google.api import get_google
from tuttitrip.shared.google.schemas import GoogleAccessError, GoogleErrorCode
from tuttitrip.shared.google.services.google_token import (
    GoogleAccess,
    GoogleTokenSource,
)
from tuttitrip.shared.jobs.api import get_job_queue
from tuttitrip.shared.jobs.contracts import (
    Workflow,
    WriteJustificationsInput,
)
from tuttitrip.shared.jobs.schemas import JobState
from tuttitrip.shared.jobs.services.job_queue import workflow_id_for

pytestmark = pytest.mark.integration

TOKEN = "ya29.integration-token"
EVENT_PATH = re.compile(r"^/calendar/v3/calendars/([^/]+)/events(?:/([0-9a-f]+))?$")


class FakeTokens(GoogleTokenSource):
    """Hands out a token, or fails like Auth0 does when Google is not connected."""

    def __init__(self) -> None:
        self.failure: GoogleErrorCode | None = None

    @override
    async def access_token(self, sub: str) -> str:
        if self.failure is not None:
            raise GoogleAccessError(self.failure, "fake")
        return TOKEN


class GoogleStub:
    """Calendar and Drive as far as the export uses them; remembers what it holds."""

    def __init__(self) -> None:
        self.calendars: dict[str, dict[str, dict[str, Any]]] = {}
        self.files: dict[str, str] = {}
        self.titles: dict[str, str] = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        path, method = request.url.path, request.method
        if path == "/calendar/v3/calendars" and method == "POST":
            calendar_id = f"cal{len(self.calendars)}"
            self.calendars[calendar_id] = {}
            self.titles[calendar_id] = json.loads(request.content)["summary"]
            return httpx.Response(200, json={"id": calendar_id})
        if match := EVENT_PATH.match(path):
            return self._events(request, match.group(1), match.group(2))
        if path.startswith("/upload/drive/v3/files"):
            return self._upload(request)
        return httpx.Response(404, json={"error": {}})

    def _events(
        self, request: httpx.Request, calendar_id: str, event_id: str | None
    ) -> httpx.Response:
        events = self.calendars.get(calendar_id)
        if events is None:
            return httpx.Response(404, json={"error": {}})
        body = json.loads(request.content) if request.content else {}
        if request.method == "POST":
            if body["id"] in events:
                return httpx.Response(409, json={"error": {}})
            events[body["id"]] = body
        elif event_id not in events:
            return httpx.Response(404, json={"error": {}})
        elif request.method == "PUT":
            events[event_id] = {**body, "id": event_id}
        else:
            del events[event_id]
        return httpx.Response(200, json={})

    def _upload(self, request: httpx.Request) -> httpx.Response:
        html = request.content.decode().split("\r\n\r\n", 2)[2].split("\r\n--")[0]
        if request.method == "POST":
            file_id = f"file{len(self.files)}"
        else:
            file_id = request.url.path.rsplit("/", 1)[1]
            if file_id not in self.files:
                return httpx.Response(404, json={"error": {}})
        self.files[file_id] = html
        return httpx.Response(
            200, json={"id": file_id, "webViewLink": f"https://docs.test/{file_id}"}
        )

    def events_total(self) -> int:
        return sum(len(events) for events in self.calendars.values())


async def _approve(app: FastAPI, http: httpx.AsyncClient, base: str) -> None:
    authorize(app, HOST)
    sent = await http.post(f"{base}/proposals")
    assert sent.status_code == 201, sent.text
    for user in (MEMBER, CO_HOST):
        authorize(app, user)
        done = await http.put(
            f"{base}/proposals/{sent.json()['id']}/response",
            json={"decision": "approve"},
        )
        assert done.status_code == 200, done.text
    authorize(app, HOST)


async def _exports_story(  # ruff: ignore[too-many-statements, too-many-locals] one story
    app: FastAPI, http: httpx.AsyncClient, tokens: FakeTokens, google: GoogleStub
) -> None:
    base = await _new_trip(http)
    plans = f"{base}/plans"
    try:
        plan = (await http.post(plans)).json()
        stops = sum(len(d["items"]) for d in plan["days"])
        calendar = f"{plans}/{plan['id']}/google/calendar"
        drive = f"{plans}/{plan['id']}/google/drive"

        # An unapproved plan is not exported, to either place.
        for url in (calendar, drive):
            refused = await http.post(url)
            assert refused.status_code == 409
            assert refused.json()["detail"]["code"] == "plan.not_approved"
        assert google.requests == []
        await _approve(app, http, base)

        # Google is not connected (or lost the scope, or the token expired):
        # 409 with a code, the scope to ask for and the .ics fallback.
        fallback = ""
        for code in GoogleErrorCode:
            if code is GoogleErrorCode.UNAVAILABLE:
                continue
            tokens.failure = code
            asked = await http.post(calendar)
            assert asked.status_code == 409, asked.text
            detail = asked.json()["detail"]
            assert detail["code"] == code.value
            assert detail["required_scope"].endswith("/calendar.app.created")
            fallback = detail["fallback_url"]
            assert fallback.endswith(f"/plans/{plan['id']}/calendar.ics")
            asked = await http.post(drive)
            assert asked.json()["detail"]["required_scope"].endswith("/drive.file")
        assert (await http.get(fallback)).status_code == 200
        tokens.failure = GoogleErrorCode.UNAVAILABLE
        assert (await http.post(calendar)).status_code == 502
        tokens.failure = None
        assert google.requests == []

        # Saving to Google Calendar: a calendar named after the trip, one event a stop.
        saved = await http.post(calendar)
        assert saved.status_code == 200, saved.text
        assert saved.json()["created"] is True
        assert saved.json()["events"] == stops > 0
        assert google.events_total() == stops
        assert next(iter(google.titles.values())).startswith("TuttiTrip: ")

        # Again: the same events are updated, nothing is duplicated.
        again = await http.post(calendar)
        assert again.status_code == 200
        assert again.json()["created"] is False
        assert again.json()["calendar_id"] == saved.json()["calendar_id"]
        assert len(google.calendars) == 1
        assert google.events_total() == stops

        # The user deleted the calendar: it is made again.
        google.calendars.clear()
        remade = await http.post(calendar)
        assert remade.status_code == 200
        assert remade.json()["created"] is True
        assert google.events_total() == stops

        # Each member saves to their own account; a stranger is a 404.
        authorize(app, MEMBER)
        assert (await http.post(calendar)).status_code == 200
        assert len(google.calendars) == 2
        authorize(app, OUTSIDER)
        assert (await http.post(calendar)).status_code == 404
        authorize(app, HOST)

        # Drive: one document with a link, replaced on the next export.
        exported = await http.post(drive)
        assert exported.status_code == 200, exported.text
        assert exported.json()["created"] is True
        link = exported.json()["web_view_link"]
        assert link.startswith("https://docs.test/")
        (html,) = google.files.values()
        assert html.count("<h2>") == len(plan["days"])
        replaced = await http.post(drive)
        assert replaced.json()["created"] is False
        assert replaced.json()["web_view_link"] == link
        assert len(google.files) == 1
        assert (
            await http.post(f"{plans}/{uuid.uuid4()}/google/drive")
        ).status_code == 404
    finally:
        authorize(app, HOST)
        await http.delete(base)


async def _anyway_story(
    app: FastAPI, http: httpx.AsyncClient, queue: FakeJobQueue
) -> None:
    base = await _new_trip(http)
    plans = f"{base}/plans"
    try:
        plan = (await http.post(plans)).json()
        assert plan["anyway"] == []  # every accepted place is in a three-day plan
        anyway = f"{plans}/{plan['id']}/anyway"
        empty = await http.get(anyway)
        assert empty.status_code == 200
        assert empty.json()["suggestions"] == []

        # Put a suggestion into the stored result, as the computation would.
        place = plan["days"][1]["items"][0]
        item = {
            "place_id": place["place_id"],
            "name": place["name"],
            "day": 2,
            "v_p": 0.05,
            "effects": {"d_min_r": -0.04, "d_cost": "35.00", "d_minutes": 90},
            "justification": "Szablon.",
            "justification_source": "template",
            "status": "proposed",
        }
        async with get_sessionmaker()() as session:
            row = await session.get(PlanVersion, uuid.UUID(plan["id"]))
            assert row is not None
            await session.execute(
                update(PlanVersion)
                .where(PlanVersion.id == row.id)
                .values(result={**row.result, "anyway": [item]})
            )
            await session.commit()

        # The plan carries it: one for the day, with the cost and the template.
        got = (await http.get(f"{plans}/latest")).json()
        assert [a["day"] for a in got["anyway"]] == [2]
        assert got["anyway"][0]["justification_source"] == "template"
        listed = (await http.get(anyway)).json()
        assert listed["justification_pending"] is True

        # The model's text arrives from the worker job (same id, no second run).
        job = workflow_id_for(
            Workflow.WRITE_JUSTIFICATIONS,
            plan["id"],
            WriteJustificationsInput(plan_id=uuid.UUID(plan["id"])),
        )
        queue.jobs[job] = JobState(
            workflow_id=job,
            workflow_name=Workflow.WRITE_JUSTIFICATIONS.value,
            status="SUCCESS",
            output={
                "justifications": [
                    {
                        "place_id": place["place_id"],
                        "profile_id": None,
                        "text": "Warto zobaczyć, choć nie jest w waszym stylu.",
                        "source": "model",
                    }
                ]
            },
        )
        modelled = (await http.get(anyway)).json()
        assert modelled["justification_pending"] is False
        assert modelled["suggestions"][0]["justification_source"] == "model"
        assert "Warto zobaczyć" in modelled["suggestions"][0]["justification"]
        assert (await http.get(f"{plans}/latest")).json()["anyway"][0][
            "justification_source"
        ] == "model"

        # Only the host rejects; the suggestion does not come back on its day.
        authorize(app, MEMBER)
        assert (
            await http.post(f"{anyway}/{place['place_id']}/reject")
        ).status_code == 403
        authorize(app, HOST)
        assert (await http.post(f"{anyway}/{uuid.uuid4()}/reject")).status_code == 404
        rejected = await http.post(f"{anyway}/{place['place_id']}/reject")
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["suggestions"] == []
        recomputed = await http.post(plans)
        assert recomputed.json()["id"] == plan["id"]
        assert recomputed.json()["anyway"] == []
        async with get_sessionmaker()() as session:
            states = (
                await session.scalars(
                    select(AnywayState).where(
                        AnywayState.trip_id == uuid.UUID(plan["trip_id"])
                    )
                )
            ).all()
            assert [(s.day, s.rejected_by_sub) for s in states] == [(2, HOST.sub)]
    finally:
        authorize(app, HOST)
        await http.delete(base)


def test_google_exports_and_anyway_end_to_end() -> None:
    app = create_app()
    authorize(app, HOST)
    tokens, google, queue = FakeTokens(), GoogleStub(), FakeJobQueue()
    stub_http = httpx.AsyncClient(transport=httpx.MockTransport(google))
    access = GoogleAccess(tokens, stub_http)
    overrides: Mapping[Any, Any] = {
        get_google: lambda: access,
        get_job_queue: lambda: queue,
    }
    app.dependency_overrides.update(overrides)

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
                    await _exports_story(app, http, tokens, google)
                    await _anyway_story(app, http, queue)
            finally:
                await unseed_city()
        finally:
            await stub_http.aclose()
            await dispose_engine()

    asyncio.run(run())
