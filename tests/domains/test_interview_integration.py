"""Interview sessions against a real PostgreSQL (``pytest -m integration``).

Needs the database of ``docker compose up -d db`` with ``alembic upgrade head``.
"""

import uuid
from collections.abc import Awaitable, Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlalchemy import select

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.interview.models import InterviewSession
from tuttitrip.interview.schemas import FieldRef, KnowledgeField
from tuttitrip.interview.services import session_service
from tuttitrip.main import create_app
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.session import get_sessionmaker
from tuttitrip.trips.models import TripMember
from tuttitrip.trips.schemas import TripMembership, TripRole

pytestmark = pytest.mark.integration

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
