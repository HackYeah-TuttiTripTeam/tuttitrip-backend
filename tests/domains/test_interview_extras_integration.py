"""Draft plan (#60), measured questions (#62) and a member's interview (#91).

Run with ``pytest -m integration`` after ``alembic upgrade head``. The city and
places of the fixtures are written to the database and removed at the end.
"""

import json
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo
from sqlalchemy import select

from tests.fixtures.city import CITY_SLUG
from tests.shared.db_seed import seed_city, unseed_city
from tests.shared.fakes import authorize
from tests.shared.interview_world import model_of
from tests.shared.paths import path
from tuttitrip.interview.models import InterviewSession
from tuttitrip.interview.services import question_service, session_service
from tuttitrip.interview.services.interview_agent import interview_agent
from tuttitrip.main import create_app
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.session import get_sessionmaker
from tuttitrip.trips.models import TripMember
from tuttitrip.trips.schemas import TripMembership, TripRole

pytestmark = [
    pytest.mark.integration,
    pytest.mark.filterwarnings(
        "ignore::pydantic_ai_harness.spend.UnpricedModelWarning"
    ),
]

HOST = AuthenticatedUser(sub=f"auth0|host-{uuid.uuid4()}")
MEMBER = AuthenticatedUser(sub=f"auth0|member-{uuid.uuid4()}")
TODAY = date(2026, 10, 7)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    authorize(app, HOST)
    with TestClient(app) as test_client:
        _run(test_client, seed_city)
        try:
            yield test_client
        finally:
            _run(test_client, unseed_city)


def _run[T](client: TestClient, work: Callable[[], Awaitable[T]]) -> T:
    assert client.portal is not None
    return client.portal.call(work)


def _as(client: TestClient, user: AuthenticatedUser) -> None:
    authorize(client.app, user)  # ty: ignore[invalid-argument-type]


def _trip(client: TestClient, **fields: Any) -> str:  # ruff: ignore[any-type]
    body = {"name": "Weekend", "destination": "Miasto Testowe", **fields}
    response = client.post(path("create_trip"), json=body)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _with_city(client: TestClient) -> str:
    trip = _trip(client, city_slug=CITY_SLUG)
    assert client.get(path("get_knowledge", trip_id=trip)).json()["trip"]["city_slug"]
    return trip


# --- #60: the preliminary plan --------------------------------------------------------


def test_a_city_alone_gives_a_draft_plan_with_its_assumptions(
    client: TestClient,
) -> None:
    trip = _with_city(client)
    url = path("build_draft_plan", trip_id=trip)
    first = client.post(url)
    assert first.status_code == 201, first.text
    draft = first.json()
    codes = [a["code"] for a in draft["assumptions"]]
    assert codes == ["dates", "people", "budget", "preferences"]
    assert [a["text"] for a in draft["assumptions"]][:2] == [
        draft["assumptions"][0]["text"],
        "Założyłem dwoje dorosłych.",
    ]
    plan = client.get(path("get_plan", trip_id=trip, plan_id=draft["plan_id"])).json()
    assert plan["params"]["draft"] is True
    assert len(plan["days"]) == 1
    assert plan["fairness"]["group_size"] == 2

    # The trip itself is untouched: the assumptions were made in memory.
    knowledge = client.get(path("get_knowledge", trip_id=trip)).json()
    assert knowledge["trip"]["start_date"] is None
    assert len(knowledge["people"]) == 1

    # The same data gives the same plan, and no new version.
    again = client.post(url).json()
    assert again["plan_id"] == draft["plan_id"]
    assert again["plan_hash"] == draft["plan_hash"]


def test_without_a_city_the_answer_is_podaj_miasto(client: TestClient) -> None:
    trip = _trip(client, destination=None)
    response = client.post(path("build_draft_plan", trip_id=trip))
    assert response.status_code == 422
    assert response.json()["detail"] == "Podaj miasto"


def test_the_next_answers_make_a_new_regular_version(client: TestClient) -> None:
    trip = _with_city(client)
    draft = client.post(path("build_draft_plan", trip_id=trip)).json()
    added = client.post(
        path("create_profile", trip_id=trip), json={"display_name": "Zosia", "age": 6}
    )
    assert added.status_code == 201, added.text
    patched = client.patch(
        path("update_trip", trip_id=trip),
        json={"start_date": "2026-10-10", "end_date": "2026-10-11"},
    )
    assert patched.status_code == 200, patched.text
    real = client.post(path("create_plan", trip_id=trip))
    assert real.status_code == 201, real.text
    assert real.json()["version"] == draft["version"] + 1
    assert real.json()["params"]["draft"] is False
    # The draft stays as it was: a plan is never edited, a new version is made.
    old = client.get(path("get_plan", trip_id=trip, plan_id=draft["plan_id"])).json()
    assert old["params"]["draft"] is True
    assert old["plan_hash"] == draft["plan_hash"]


# --- #62: the measured question -------------------------------------------------------


def test_the_next_question_is_chosen_by_its_impact_and_repeats(
    client: TestClient,
) -> None:
    trip = _with_city(client)
    membership = TripMembership(
        trip_id=uuid.UUID(trip), sub=HOST.sub, role=TripRole.HOST
    )

    async def choose() -> Any:  # ruff: ignore[any-type]
        async with get_sessionmaker()() as session:
            known = await session_service.get_knowledge(session, membership)
            return await question_service.choose(
                session, membership, known, set(), TODAY
            )

    first = _run(client, choose)
    second = _run(client, choose)
    assert first is not None
    assert first.impact is not None
    assert first.impact > 0
    assert first == second


# --- #91: a member's interview --------------------------------------------------------


def _join(client: TestClient, trip: str) -> str:
    async def add() -> None:
        async with get_sessionmaker()() as session:
            session.add(
                TripMember(
                    trip_id=uuid.UUID(trip), user_sub=MEMBER.sub, role=TripRole.MEMBER
                )
            )
            await session.commit()

    _run(client, add)
    made = client.post(
        path("create_profile", trip_id=trip),
        json={"display_name": "Gość", "age": 30, "user_sub": MEMBER.sub},
    )
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


def test_a_members_session_is_their_own_and_their_tools_write_only_to_them(
    client: TestClient,
) -> None:
    trip = _with_city(client)
    host_thread = client.post(path("start_session", trip_id=trip)).json()["id"]
    member_profile = _join(client, trip)
    other = client.post(
        path("create_profile", trip_id=trip), json={"display_name": "Ania", "age": 40}
    ).json()["id"]

    _as(client, MEMBER)
    started = client.post(path("start_session", trip_id=trip))
    assert started.status_code == 201, started.text
    thread = started.json()["id"]
    assert thread != host_thread
    assert client.post(path("start_session", trip_id=trip)).json()["id"] == thread

    panel = client.get(path("get_knowledge", trip_id=trip)).json()
    assert [p["id"] for p in panel["people"]] == [member_profile]
    assert panel["trip"]["budget_total_max"] is None
    assert client.post(path("build_draft_plan", trip_id=trip)).status_code == 403

    moves = iter(
        [
            ToolCallPart("add_my_interest", {"interest": "science"}),
            "Zapisałem muzea techniki.",
        ]
    )

    def respond(_m: list[ModelMessage], _i: AgentInfo) -> ModelResponse:
        move = next(moves)
        return ModelResponse(
            parts=[TextPart(move)] if isinstance(move, str) else [move]
        )

    def turn(thread_id: str, text: str = "lubię muzea techniki") -> Any:  # ruff: ignore[any-type]
        body = {
            "threadId": thread_id,
            "runId": uuid.uuid4().hex,
            "state": {},
            "messages": [{"id": "m", "role": "user", "content": text}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
        return client.post(path("run_turn", trip_id=trip), json=body)

    # The host's session id is not the member's to use.
    assert turn(host_thread).status_code == 404
    with interview_agent.override(model=model_of(respond)):
        response = turn(thread)
    assert response.status_code == 200, response.text
    kinds = [
        json.loads(line.removeprefix("data: "))["type"]
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert kinds[-1] == "RUN_FINISHED"

    mine = client.get(path("get_current_session", trip_id=trip)).json()
    assert [m["text"] for m in mine["messages"]["items"]] == [
        "lubię muzea techniki",
        "Zapisałem muzea techniki.",
    ]

    _as(client, HOST)
    saved = client.get(
        path("get_preferences", trip_id=trip, profile_id=member_profile)
    ).json()
    assert saved["interests"] == {"science": 1.0}
    untouched = client.get(
        path("get_preferences", trip_id=trip, profile_id=other)
    ).json()
    assert untouched["interests"] == {}
    # The host's own session has none of the member's conversation.
    theirs = client.get(path("get_current_session", trip_id=trip)).json()
    assert theirs["id"] == host_thread
    assert theirs["messages"]["items"] == []


def test_one_open_session_per_member_and_one_for_the_trip(client: TestClient) -> None:
    trip = _with_city(client)
    client.post(path("start_session", trip_id=trip))
    _join(client, trip)
    _as(client, MEMBER)
    client.post(path("start_session", trip_id=trip))

    async def count() -> list[Any]:
        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(InterviewSession.profile_id).where(
                    InterviewSession.trip_id == uuid.UUID(trip)
                )
            )
            return sorted(rows.all(), key=lambda p: p is not None)

    owners = _run(client, count)
    assert len(owners) == 2
    assert owners[0] is None
    assert owners[1] is not None
