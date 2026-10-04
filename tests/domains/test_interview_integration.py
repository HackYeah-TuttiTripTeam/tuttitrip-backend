"""Interview sessions against a real PostgreSQL (``pytest -m integration``).

Needs the database of ``docker compose up -d db`` with ``alembic upgrade head``.
"""

import json
import uuid
from collections.abc import Awaitable, Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo
from sqlalchemy import select

from tests.shared.fakes import authorize
from tests.shared.interview_world import model_of
from tests.shared.paths import path
from tuttitrip.interview.models import InterviewSession
from tuttitrip.interview.schemas import FieldRef, KnowledgeField
from tuttitrip.interview.services import session_service
from tuttitrip.interview.services.interview_agent import interview_agent
from tuttitrip.main import create_app
from tuttitrip.profiles.logic.age_defaults import DEFAULTS
from tuttitrip.profiles.schemas import AgeGroup
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
OUTSIDER = AuthenticatedUser(sub=f"auth0|out-{uuid.uuid4()}")
MEMBER = AuthenticatedUser(sub=f"auth0|member-{uuid.uuid4()}")


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    authorize(app, HOST)
    with TestClient(app) as test_client:
        yield test_client


def _run[T](client: TestClient, work: Callable[[], Awaitable[T]]) -> T:
    # The engine lives on the TestClient's loop, so work runs there too.
    assert client.portal is not None
    return client.portal.call(work)


def _as(client: TestClient, user: AuthenticatedUser) -> None:
    authorize(client.app, user)  # ty: ignore[invalid-argument-type]


def _trip(client: TestClient) -> str:
    response = client.post(path("create_trip"), json={"name": "Weekend"})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_session_is_resumed_and_history_round_trips(client: TestClient) -> None:
    trip = _trip(client)
    url = path("start_session", trip_id=trip)
    first, second = client.post(url), client.post(url)
    assert (first.status_code, second.status_code) == (201, 200)
    thread = first.json()["id"]
    assert second.json()["id"] == thread

    messages = [
        ModelRequest(parts=[UserPromptPart(content="Jedziemy do Krakowa")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="save", args={"destination": "Kraków"})]
        ),
        ModelRequest(parts=[ToolReturnPart(tool_name="save", content="ok")]),
        ModelResponse(parts=[TextPart(content="Ile osób jedzie?")]),
    ]
    membership = TripMembership(
        trip_id=uuid.UUID(trip), sub=HOST.sub, role=TripRole.HOST
    )

    async def store_and_load() -> object:
        async with get_sessionmaker()() as session:
            await session_service.append_messages(
                session, membership, uuid.UUID(thread), messages[:2]
            )
            await session_service.append_messages(
                session, membership, uuid.UUID(thread), messages[2:]
            )
        async with get_sessionmaker()() as session:
            return await session_service.load_history(
                session, membership, uuid.UUID(thread)
            )

    loaded = _run(client, store_and_load)
    assert loaded == messages
    assert (
        ModelMessagesTypeAdapter.validate_python(
            ModelMessagesTypeAdapter.dump_python(messages, mode="json")
        )
        == messages
    )

    current = client.get(path("get_current_session", trip_id=trip)).json()
    assert current["id"] == thread
    assert [m["text"] for m in current["messages"]["items"]] == [
        "Jedziemy do Krakowa",
        "Ile osób jedzie?",
    ]


def test_knowledge_reads_the_domains_and_tells_host_from_assistant(
    client: TestClient,
) -> None:
    trip = _trip(client)
    patch = client.patch(
        path("update_trip", trip_id=trip),
        json={
            "destination": "Kraków",
            "currency": "PLN",
            "budget_total_min": "1000",
            "budget_total_max": "2000",
        },
    )
    assert patch.status_code == 200, patch.text
    added = client.post(
        path("create_profile", trip_id=trip), json={"display_name": "Ola", "age": 8}
    )
    assert added.status_code == 201, added.text

    body = client.get(path("get_knowledge", trip_id=trip)).json()
    assert body["trip"]["destination"] == "Kraków"
    assert len(body["people"]) == 2
    missing = {m["field"] for m in body["missing"]}
    assert missing == {"dates", "preferences"}
    assert {s["source"] for s in body["sources"]} == {"host"}

    membership = TripMembership(
        trip_id=uuid.UUID(trip), sub=HOST.sub, role=TripRole.HOST
    )

    async def mark() -> None:
        async with get_sessionmaker()() as session:
            await session_service.mark_assistant_values(
                session,
                membership,
                [
                    FieldRef(field=KnowledgeField.DESTINATION),
                    FieldRef(field=KnowledgeField.BUDGET),
                ],
            )

    _run(client, mark)
    sources = {
        s["field"]: s["source"]
        for s in client.get(path("get_knowledge", trip_id=trip)).json()["sources"]
        if s["profile_id"] is None
    }
    assert sources == {"destination": "assistant", "budget": "assistant"}

    client.patch(path("update_trip", trip_id=trip), json={"destination": "Gdańsk"})
    after = {
        s["field"]: s["source"]
        for s in client.get(path("get_knowledge", trip_id=trip)).json()["sources"]
        if s["profile_id"] is None
    }
    assert after == {"destination": "host", "budget": "assistant"}


def test_outsider_gets_404_and_member_403(client: TestClient) -> None:
    trip = _trip(client)

    async def add_member() -> None:
        async with get_sessionmaker()() as session:
            session.add(
                TripMember(
                    trip_id=uuid.UUID(trip), user_sub=MEMBER.sub, role=TripRole.MEMBER
                )
            )
            await session.commit()

    _run(client, add_member)
    routes = [
        ("post", path("start_session", trip_id=trip)),
        ("get", path("get_current_session", trip_id=trip)),
        ("get", path("get_knowledge", trip_id=trip)),
    ]
    _as(client, OUTSIDER)
    assert [getattr(client, m)(u).status_code for m, u in routes] == [404] * 3
    _as(client, MEMBER)
    assert [getattr(client, m)(u).status_code for m, u in routes] == [403] * 3


def test_sessions_disappear_with_the_trip(client: TestClient) -> None:
    trip = _trip(client)
    client.post(path("start_session", trip_id=trip))
    assert client.delete(path("delete_trip", trip_id=trip)).status_code == 204

    async def count() -> int:
        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(InterviewSession).where(
                    InterviewSession.trip_id == uuid.UUID(trip)
                )
            )
            return len(rows.all())

    assert _run(client, count) == 0


def test_a_turn_fills_the_trip_through_the_services_and_is_remembered(
    client: TestClient,
) -> None:
    # The demo sentence, a scripted model and a real database: the tools write
    # through the services, the stream carries the snapshots, the next turn
    # starts from the stored history.
    trip = _trip(client)
    thread = client.post(path("start_session", trip_id=trip)).json()["id"]
    seen: list[list[ModelMessage]] = []
    moves = iter(
        [
            ToolCallPart(
                "set_trip_basics",
                {"city": "Gdańsk", "start_date": "2026-10-10", "days": 3},
            ),
            ToolCallPart("add_person", {"name": "Zosia", "age": 6}),
            ToolCallPart("add_person", {"name": "Kuba", "age": 13}),
            ToolCallPart("add_person", {"name": "Babcia", "age": 72}),
            "Zapisałem Gdańsk i troje domowników.",
            "Wszystko gra.",
        ]
    )

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        move = next(moves)
        return ModelResponse(
            parts=[TextPart(move)] if isinstance(move, str) else [move]
        )

    def turn(text: str) -> list[str]:
        body = {
            "threadId": thread,
            "runId": uuid.uuid4().hex,
            "state": {},
            "messages": [{"id": "m", "role": "user", "content": text}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
        response = client.post(path("run_turn", trip_id=trip), json=body)
        assert response.status_code == 200, response.text
        return [
            json.loads(line.removeprefix("data: "))["type"]
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]

    with interview_agent.override(model=model_of(respond)):
        kinds = turn("Gdańsk, trzy dni, dzieci 6 i 13 lat, babcia")
        turn("Dzięki")

    assert kinds[0] == "RUN_STARTED"
    assert kinds[-1] == "RUN_FINISHED"
    assert kinds.count("STATE_SNAPSHOT") == 4

    knowledge = client.get(path("get_knowledge", trip_id=trip)).json()
    assert knowledge["trip"]["destination"] == "Gdańsk"
    assert (knowledge["trip"]["start_date"], knowledge["trip"]["end_date"]) == (
        "2026-10-10",
        "2026-10-12",
    )
    assert len(knowledge["people"]) == 4
    by_name = {p["display_name"]: p for p in knowledge["people"]}
    assert by_name["Babcia"]["age_group"] == "senior"
    assert by_name["Zosia"]["segment_km"] == DEFAULTS[AgeGroup.CHILD].segment_km
    assert by_name["Babcia"]["segment_km"] == DEFAULTS[AgeGroup.SENIOR].segment_km
    sources = {(s["field"], s["profile_id"]): s["source"] for s in knowledge["sources"]}
    assert sources["destination", None] == "assistant"
    assert sources["people", by_name["Zosia"]["id"]] == "assistant"

    current = client.get(
        path("get_current_session", trip_id=trip), params={"size": 50}
    ).json()
    assert [m["text"] for m in current["messages"]["items"]] == [
        "Gdańsk, trzy dni, dzieci 6 i 13 lat, babcia",
        "Zapisałem Gdańsk i troje domowników.",
        "Dzięki",
        "Wszystko gra.",
    ]
    second_turn = seen[-1]
    assert any(
        isinstance(part, UserPromptPart) and part.content == "Dzięki"
        for message in second_turn
        for part in message.parts
    )
    assert any(
        isinstance(part, ToolCallPart) and part.tool_name == "add_person"
        for message in second_turn
        for part in message.parts
    )
