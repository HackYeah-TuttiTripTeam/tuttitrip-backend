"""Interview sessions: history round trip, display, knowledge, routes and access."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.interview import db
from tuttitrip.interview.logic import knowledge
from tuttitrip.interview.models import InterviewSession
from tuttitrip.interview.schemas import (
    FieldRef,
    KnowledgeField,
    KnowledgeRead,
    MessageRole,
    MessagesQuery,
    SessionStatus,
    ValueSource,
)
from tuttitrip.interview.services import session_service
from tuttitrip.main import create_app
from tuttitrip.profiles.preferences.schemas import ImportancePool, PreferencesRead
from tuttitrip.profiles.schemas import AgeGroup, ProfileRead
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import MemberStatus, TripMembership, TripRead, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError, TripRoleError

TRIP = uuid.uuid4()
ME = AuthenticatedUser(sub="auth0|me")
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
HOST = TripMembership(trip_id=TRIP, sub=ME.sub, role=TripRole.HOST)


def _history() -> list[ModelMessage]:
    return [
        ModelRequest(
            parts=[
                SystemPromptPart(content="secret rules"),
                UserPromptPart(content="Jedziemy do Krakowa", timestamp=NOW),
            ]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="save_trip", args={"destination": "Kraków"}),
            ],
            timestamp=NOW,
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="save_trip", content="ok", timestamp=NOW)]
        ),
        ModelResponse(
            parts=[TextPart(content="Ile osób jedzie?")],
            timestamp=NOW,
        ),
    ]


def _trip(**changes: Any) -> TripRead:  # ruff: ignore[any-type]
    data: dict[str, Any] = {
        "id": TRIP,
        "name": "Weekend",
        "destination": None,
        "created_at": NOW,
        "start_date": None,
        "end_date": None,
        "day_start": "09:00",
        "day_end": "19:00",
        "city_slug": None,
        "currency": None,
        "budget_total_min": None,
        "budget_total_max": None,
        "budget_day_min": None,
        "budget_day_max": None,
        "budget_flex_pct": 10,
        "fairness_alpha": 1.0,
        "my_role": TripRole.HOST,
        "my_status": MemberStatus.CONFIRMED,
    } | changes
    return TripRead(**data)


def _person(name: str = "Ola") -> ProfileRead:
    return ProfileRead(
        id=uuid.uuid4(),
        trip_id=TRIP,
        display_name=name,
        age=30,
        age_group=AgeGroup.ADULT,
        user_sub=None,
        weight=1.0,
        segment_km=3,
        daily_km=10,
        active_min=300,
        stairs_sensitivity=0.1,
        queue_patience_min=20,
        nap_start=None,
        nap_minutes=0,
        floor=30,
    )


def _prefs(person: ProfileRead, *, filled: bool) -> PreferencesRead:
    return PreferencesRead(
        profile_id=person.id,
        importance_pool=ImportancePool(
            lodging=2, food=2, attractions=3, pace=1, cost=2
        ),
        constraints=None,
        effective_stairs_sensitivity=None,
        filled=filled,
        updated_by_sub=None,
        updated_at=None,
    )


# --- history ---------------------------------------------------------------


def test_history_with_tool_calls_survives_a_json_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = InterviewSession(id=uuid.uuid4(), trip_id=TRIP, history=[])
    session = AsyncMock()
    monkeypatch.setattr(db, "select_session", AsyncMock(return_value=row))
    original = _history()

    async def store() -> None:
        await session_service.append_messages(session, HOST, row.id, original[:2])
        await session_service.append_messages(session, HOST, row.id, original[2:])

    asyncio.run(store())
    assert row.history == ModelMessagesTypeAdapter.dump_python(original, mode="json")
    assert ModelMessagesTypeAdapter.validate_python(row.history) == original
    assert session.commit.await_count == 2


def test_agent_run_with_user_prompt_stores_the_user_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The #56 contract: the host's text is `user_prompt`, the stored history is
    # `message_history`, and `new_messages()` is what gets appended.
    agent = Agent(TestModel(custom_output_text="Ile osób jedzie?"))
    row = InterviewSession(id=uuid.uuid4(), trip_id=TRIP, history=[])
    session = AsyncMock()
    monkeypatch.setattr(db, "select_session", AsyncMock(return_value=row))

    async def turn(text: str) -> None:
        stored = await session_service.load_history(session, HOST, row.id)
        result = await agent.run(text, message_history=stored)
        await session_service.append_messages(
            session, HOST, row.id, result.new_messages()
        )

    async def talk() -> list[ModelMessage]:
        await turn("Jedziemy do Krakowa")
        await turn("Nas jest czworo")
        return await session_service.load_history(session, HOST, row.id)

    loaded = asyncio.run(talk())
    shown = session_service._display(loaded)  # ruff: ignore[private-member-access]
    assert [(m.role, m.text) for m in shown] == [
        (MessageRole.USER, "Jedziemy do Krakowa"),
        (MessageRole.ASSISTANT, "Ile osób jedzie?"),
        (MessageRole.USER, "Nas jest czworo"),
        (MessageRole.ASSISTANT, "Ile osób jedzie?"),
    ]
    assert len(loaded) == 4


def test_unreadable_stored_history_fails_clearly() -> None:
    row = InterviewSession(id=uuid.uuid4(), trip_id=TRIP, history=[{"kind": "nope"}])
    with pytest.raises(session_service.HistoryIncompatibleError):
        session_service._history(row)  # ruff: ignore[private-member-access]


def test_text_around_a_tool_call_is_one_assistant_message() -> None:
    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="Cześć")]),
        ModelResponse(
            parts=[
                TextPart(content="Sprawdzam."),
                ToolCallPart(tool_name="save", args={}),
            ]
        ),
        ModelRequest(parts=[ToolReturnPart(tool_name="save", content="ok")]),
        ModelResponse(parts=[TextPart(content="Gotowe.")]),
    ]
    shown = session_service._display(history)  # ruff: ignore[private-member-access]
    assert [(m.role, m.text) for m in shown] == [
        (MessageRole.USER, "Cześć"),
        (MessageRole.ASSISTANT, "Sprawdzam.\n\nGotowe."),
    ]


def test_append_to_a_session_of_another_trip_is_not_found() -> None:
    session = AsyncMock()
    session.scalar.return_value = None
    with pytest.raises(session_service.SessionNotFoundError):
        asyncio.run(
            session_service.append_messages(session, HOST, uuid.uuid4(), _history())
        )


def test_display_shows_only_questions_and_answers() -> None:
    shown = session_service._display(_history())  # ruff: ignore[private-member-access]
    assert [(m.position, m.role, m.text) for m in shown] == [
        (0, MessageRole.USER, "Jedziemy do Krakowa"),
        (1, MessageRole.ASSISTANT, "Ile osób jedzie?"),
    ]


def _row() -> InterviewSession:
    history = ModelMessagesTypeAdapter.dump_python(_history(), mode="json")
    return InterviewSession(
        id=uuid.uuid4(),
        trip_id=TRIP,
        status=SessionStatus.OPEN,
        created_by=ME.sub,
        created_at=NOW,
        updated_at=NOW,
        history=history,
    )


def test_current_pages_filters_and_sorts_the_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db, "select_open_session", AsyncMock(return_value=_row()))
    session = AsyncMock()

    def current(**query: Any) -> Any:  # ruff: ignore[any-type]
        return asyncio.run(
            session_service.get_current(session, HOST, MessagesQuery(**query))
        )

    everything = current()
    assert everything.message_count == 2
    assert everything.messages.total == 2
    newest = current(dir="desc", size=1)
    assert [m.position for m in newest.messages.items] == [1]
    assert newest.messages.pages == 2
    only_user = current(role="user")
    assert [m.role for m in only_user.messages.items] == [MessageRole.USER]
    past_end = current(page=5)
    assert past_end.messages.items == []
    assert past_end.messages.total == 2


# --- knowledge -------------------------------------------------------------


def test_empty_trip_misses_everything_and_a_second_person() -> None:
    host = _person("Host")
    current = knowledge.values(_trip(), [host], [_prefs(host, filled=False)])
    missing = knowledge.missing(current)
    assert {(r.field, r.profile_id) for r in missing} == {
        (KnowledgeField.DESTINATION, None),
        (KnowledgeField.DATES, None),
        (KnowledgeField.BUDGET, None),
        (KnowledgeField.PREFERENCES, host.id),
        (KnowledgeField.PEOPLE, None),
    }


def test_two_people_and_a_budget_leave_only_the_rest_missing() -> None:
    ana, bob = _person("Ana"), _person("Bob")
    trip = _trip(
        destination="Kraków",
        currency="PLN",
        budget_total_min=Decimal(1000),
        budget_total_max=Decimal(2000),
    )
    current = knowledge.values(
        trip, [ana, bob], [_prefs(ana, filled=True), _prefs(bob, filled=False)]
    )
    assert {(r.field, r.profile_id) for r in knowledge.missing(current)} == {
        (KnowledgeField.DATES, None),
        (KnowledgeField.PREFERENCES, bob.id),
    }


def test_source_is_assistant_until_the_host_changes_the_value() -> None:
    ref = FieldRef(field=KnowledgeField.DESTINATION)
    written = knowledge.values(_trip(destination="Kraków"), [], [])
    stored = {ref: written[ref].digest}
    same = knowledge.sources(written, stored)
    assert [(s.field, s.source) for s in same] == [
        (KnowledgeField.DESTINATION, ValueSource.ASSISTANT)
    ]
    edited = knowledge.values(_trip(destination="Gdańsk"), [], [])
    assert knowledge.sources(edited, stored)[0].source is ValueSource.HOST
    assert knowledge.sources(written, {})[0].source is ValueSource.HOST


def test_dates_need_both_ends_and_budget_needs_a_currency() -> None:
    dates, budget = (
        FieldRef(field=f) for f in (KnowledgeField.DATES, KnowledgeField.BUDGET)
    )
    day = date(2026, 11, 1)
    only_start = knowledge.values(_trip(start_date=day), [], [])
    assert not only_start[dates].filled
    both = knowledge.values(_trip(start_date=day, end_date=day), [], [])
    assert both[dates].filled
    no_currency = knowledge.values(_trip(budget_day_min=Decimal(100)), [], [])
    assert not no_currency[budget].filled
    paid = knowledge.values(_trip(budget_day_min=Decimal(100), currency="PLN"), [], [])
    assert paid[budget].filled


def test_linking_an_account_does_not_change_the_person_digest() -> None:
    person = _person()
    ref = FieldRef(field=KnowledgeField.PEOPLE, profile_id=person.id)
    linked = person.model_copy(update={"user_sub": "auth0|ola"})
    assert (
        knowledge.values(_trip(), [person], [])[ref].digest
        == knowledge.values(_trip(), [linked], [])[ref].digest
    )
    renamed = person.model_copy(update={"display_name": "Ola K."})
    assert (
        knowledge.values(_trip(), [renamed], [])[ref].digest
        != knowledge.values(_trip(), [person], [])[ref].digest
    )


def test_preference_digest_ignores_who_and_when_saved() -> None:
    person = _person()
    ref = FieldRef(field=KnowledgeField.PREFERENCES, profile_id=person.id)
    first = _prefs(person, filled=True)
    later = first.model_copy(update={"updated_by_sub": "x", "updated_at": NOW})
    assert (
        knowledge.values(_trip(), [person], [first])[ref].digest
        == knowledge.values(_trip(), [person], [later])[ref].digest
    )


def test_marking_an_unfilled_value_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_service,
        "_domain_data",
        AsyncMock(return_value=(_trip(), [], [])),
    )
    with pytest.raises(session_service.UnknownFieldError):
        asyncio.run(
            session_service.mark_assistant_values(
                AsyncMock(), HOST, [FieldRef(field=KnowledgeField.DESTINATION)]
            )
        )


def test_marking_stores_the_current_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    trip = _trip(destination="Kraków")
    monkeypatch.setattr(
        session_service, "_domain_data", AsyncMock(return_value=(trip, [], []))
    )
    upsert = AsyncMock()
    monkeypatch.setattr(db, "upsert_assistant_digest", upsert)
    ref = FieldRef(field=KnowledgeField.DESTINATION)
    session = AsyncMock()
    asyncio.run(session_service.mark_assistant_values(session, HOST, [ref]))
    expected = knowledge.values(trip, [], [])[ref].digest
    upsert.assert_awaited_once_with(session, TRIP, ref, expected)
    session.commit.assert_awaited_once()


# --- routes and access -----------------------------------------------------


def _membership(role: TripRole | None) -> AsyncMock:
    def check(
        _s: object, trip_id: uuid.UUID, sub: str, min_role: TripRole
    ) -> TripMembership:
        if role is None:
            raise TripNotFoundError(str(trip_id))
        if not role.satisfies(min_role):
            raise TripRoleError(str(min_role))
        return TripMembership(trip_id=trip_id, sub=sub, role=role)

    return AsyncMock(side_effect=check)


def _fake_session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def app() -> FastAPI:
    application = create_app()
    authorize(application, ME)
    application.dependency_overrides[get_session] = _fake_session
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


ROUTES = [
    ("post", "start_session"),
    ("get", "get_current_session"),
    ("get", "get_knowledge"),
    ("post", "run_turn"),
    ("post", "voice_offer"),
]


@pytest.mark.parametrize(("method", "name"), ROUTES)
def test_outsider_gets_404_and_member_403(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    name: str,
) -> None:
    url = path(name, trip_id=TRIP)
    monkeypatch.setattr(trip_service, "get_membership", _membership(None))
    assert getattr(client, method)(url).status_code == 404
    monkeypatch.setattr(trip_service, "get_membership", _membership(TripRole.MEMBER))
    assert getattr(client, method)(url).status_code == 403


@pytest.mark.parametrize(("method", "name"), ROUTES)
def test_feature_permission_is_checked_before_the_trip(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, method: str, name: str
) -> None:
    authorize(app, ME, [Grant(Feature.TRIPS_CORE, Access.WRITE)])
    monkeypatch.setattr(trip_service, "get_membership", _membership(TripRole.HOST))
    with TestClient(app) as client:
        response = getattr(client, method)(path(name, trip_id=TRIP))
    assert response.status_code == 403
    assert "interview" in response.json()["detail"]


def test_reading_needs_only_read_but_starting_needs_write(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorize(app, ME, [Grant(Feature.INTERVIEW, Access.READ)])
    monkeypatch.setattr(trip_service, "get_membership", _membership(TripRole.HOST))
    monkeypatch.setattr(db, "select_open_session", AsyncMock(return_value=_row()))
    with TestClient(app) as client:
        assert client.post(path("start_session", trip_id=TRIP)).status_code == 403
        assert client.get(path("get_current_session", trip_id=TRIP)).status_code == 200


def test_start_is_201_then_200_with_the_same_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trip_service, "get_membership", _membership(TripRole.CO_HOST))
    row = _row()
    monkeypatch.setattr(db, "insert_open_session", AsyncMock(side_effect=[row, None]))
    monkeypatch.setattr(db, "select_open_session", AsyncMock(return_value=row))
    url = path("start_session", trip_id=TRIP)
    first, second = client.post(url), client.post(url)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"] == str(row.id)
    assert first.json()["message_count"] == 2


def test_current_without_a_session_is_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trip_service, "get_membership", _membership(TripRole.HOST))
    monkeypatch.setattr(db, "select_open_session", AsyncMock(return_value=None))
    assert client.get(path("get_current_session", trip_id=TRIP)).status_code == 404


def test_knowledge_returns_data_missing_fields_and_sources(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ana, bob = _person("Ana"), _person("Bob")
    trip = _trip(
        destination="Kraków",
        currency="PLN",
        budget_total_min=Decimal(1000),
        budget_total_max=Decimal(2000),
    )
    prefs = [_prefs(ana, filled=True), _prefs(bob, filled=False)]
    monkeypatch.setattr(trip_service, "get_membership", _membership(TripRole.HOST))
    monkeypatch.setattr(
        session_service,
        "_domain_data",
        AsyncMock(return_value=(trip, [ana, bob], prefs)),
    )
    written = knowledge.values(trip, [ana, bob], prefs)
    budget = FieldRef(field=KnowledgeField.BUDGET)
    monkeypatch.setattr(
        db,
        "select_assistant_digests",
        AsyncMock(return_value={budget: written[budget].digest}),
    )
    body = client.get(path("get_knowledge", trip_id=TRIP)).json()
    KnowledgeRead.model_validate(body)
    assert body["trip"]["destination"] == "Kraków"
    assert len(body["people"]) == 2
    assert {(m["field"], m["profile_id"]) for m in body["missing"]} == {
        ("dates", None),
        ("preferences", str(bob.id)),
    }
    by_field = {(s["field"], s["profile_id"]): s["source"] for s in body["sources"]}
    assert by_field["budget", None] == "assistant"
    assert by_field["destination", None] == "host"


def test_openapi_documents_the_interview_routes(client: TestClient) -> None:
    paths = client.get("/api/v1/openapi.json").json()["paths"]
    base = "/api/v1/trips/{trip_id}/interview"
    assert set(paths[f"{base}/sessions"]) == {"post"}
    assert set(paths[f"{base}/sessions/current"]) == {"get"}
    assert set(paths[f"{base}/knowledge"]) == {"get"}
    assert set(paths[f"{base}/agui"]) == {"post"}
    assert set(paths[f"{base}/voice/offer"]) == {"post"}
    assert set(paths[f"{base}/voice/{{call_id}}/hangup"]) == {"post"}
    assert set(paths[f"{base}/sessions"]["post"]["responses"]) >= {"200", "201"}
