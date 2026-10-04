"""Voice interview (#61): offer, sideband, hang-up and the stored transcript.

The realtime session is replaced: there is no model for realtime in the test
tools of Pydantic AI, so the real session is checked on a deployment.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, override
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.realtime import (
    RealtimeModel,
    WebRTCAnswer,
    WebRTCSession,
)

from tests.shared.fakes import authorize
from tests.shared.interview_world import World
from tests.shared.paths import path
from tuttitrip.interview.schemas import SessionStatus
from tuttitrip.interview.services import session_service, voice_service
from tuttitrip.interview.services.interview_agent import interview_agent
from tuttitrip.main import create_app
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

pytestmark = pytest.mark.usefixtures("stored")

ME = AuthenticatedUser(sub="auth0|host")
SESSION_ID = uuid.uuid4()
TRANSCRIPT: list[ModelMessage] = [
    ModelRequest(
        parts=[
            SystemPromptPart(content="secret rules"),
            UserPromptPart(content="Gdańsk, trzy dni, dzieci 6 i 13 lat"),
        ]
    ),
    ModelResponse(parts=[ToolCallPart("set_trip_basics", {})]),
    ModelResponse(parts=[TextPart("Zapisane.")]),
]


class FakeProviderSession:
    """What ``realtime.session()`` yields: events until ``end`` is set."""

    def __init__(self, end: asyncio.Event) -> None:
        self.end = end

    def __aiter__(self) -> AsyncIterator[None]:
        return self._events()

    async def _events(self) -> AsyncIterator[None]:
        await self.end.wait()
        return
        yield

    @staticmethod
    def all_messages() -> list[ModelMessage]:
        return list(TRANSCRIPT)


class FakeRealtime:
    """The accessor ``agent.realtime(...)`` returns, without a provider."""

    def __init__(
        self, *, fail_attach: bool = False, never_attach: bool = False
    ) -> None:
        self.end = asyncio.Event()
        self.fail_attach = fail_attach
        self.never_attach = never_attach
        self.calls = 0

    async def answer_webrtc_offer(self, sdp: str) -> Any:  # ruff: ignore[any-type]
        self.calls += 1
        assert sdp == "OFFER"
        return SimpleNamespace(
            sdp="ANSWER",
            session=WebRTCSession(
                provider_name="openai", session_id=f"call_{uuid.uuid4().hex[:8]}"
            ),
        )

    @asynccontextmanager
    async def session(self, *, provider_session: WebRTCSession) -> AsyncGenerator[Any]:
        assert provider_session.provider_name == "openai"
        if self.fail_attach:
            msg = "no sideband"
            raise RuntimeError(msg)
        if self.never_attach:
            await asyncio.sleep(10)
        yield FakeProviderSession(self.end)


@pytest.fixture
def stored(monkeypatch: pytest.MonkeyPatch) -> list[list[ModelMessage]]:
    appended: list[list[ModelMessage]] = []

    async def append(  # ruff: ignore[unused-async] stub of an async API
        _s: object, _m: object, session_id: uuid.UUID, messages: Sequence[ModelMessage]
    ) -> None:
        assert session_id == SESSION_ID
        appended.append(list(messages))

    monkeypatch.setattr(session_service, "append_messages", append)
    return appended


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World()
    w.install(monkeypatch)
    monkeypatch.setattr(
        trip_service,
        "get_membership",
        AsyncMock(
            side_effect=lambda _s, trip_id, sub, _r: TripMembership(
                trip_id=trip_id, sub=sub, role=TripRole.HOST
            )
        ),
    )
    session = SimpleNamespace(
        id=SESSION_ID,
        trip_id=w.trip_id,
        status=SessionStatus.OPEN,
        created_by=ME.sub,
        created_at=None,
        updated_at=None,
        message_count=0,
    )
    monkeypatch.setattr(
        session_service,
        "open_session",
        AsyncMock(return_value=(session, False)),
    )
    monkeypatch.setattr(voice_service, "get_sessionmaker", lambda: w.deps().sessions)
    monkeypatch.setattr(voice_service, "_provider_hangup", AsyncMock())
    voice_service.CALLS.clear()
    return w


def _fake_session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client() -> Any:  # ruff: ignore[any-type]
    app: FastAPI = create_app()
    authorize(app, ME)
    app.dependency_overrides[get_session] = _fake_session
    with TestClient(app) as test_client:
        yield test_client


def use(monkeypatch: pytest.MonkeyPatch, realtime: FakeRealtime) -> None:
    monkeypatch.setattr(voice_service, "realtime_for", lambda _deps: realtime)


def offer(client: TestClient, world: World, sdp: str = "OFFER") -> Any:  # ruff: ignore[any-type]
    return client.post(path("voice_offer", trip_id=world.trip_id), json={"sdp": sdp})


def hangup(client: TestClient, world: World, call_id: str) -> Any:  # ruff: ignore[any-type]
    return client.post(path("voice_hangup", trip_id=world.trip_id, call_id=call_id))


def test_offer_returns_the_answer_once_the_sideband_is_attached(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    realtime = FakeRealtime()
    use(monkeypatch, realtime)
    response = offer(client, world)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sdp"] == "ANSWER"
    call = voice_service.CALLS[body["call_id"]]
    assert call.attached.is_set()
    assert call.deps.membership.trip_id == world.trip_id
    assert call.deps.session_id == SESSION_ID
    assert hangup(client, world, body["call_id"]).status_code == 204


def test_hangup_stores_the_transcript_without_system_prompts(
    client: TestClient,
    world: World,
    stored: list[list[ModelMessage]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use(monkeypatch, FakeRealtime())
    call_id = offer(client, world).json()["call_id"]
    assert hangup(client, world, call_id).status_code == 204
    assert call_id not in voice_service.CALLS
    (transcript,) = stored
    assert len(transcript) == 3
    first = transcript[0]
    assert isinstance(first, ModelRequest)
    assert [type(p).__name__ for p in first.parts] == ["UserPromptPart"]
    voice_service._provider_hangup.assert_awaited_once_with(call_id)  # ty: ignore[unresolved-attribute]  # ruff: ignore[private-member-access]


def test_a_call_that_ends_by_itself_is_stored_and_leaves_the_registry(
    client: TestClient,
    world: World,
    stored: list[list[ModelMessage]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    realtime = FakeRealtime()
    use(monkeypatch, realtime)
    call_id = offer(client, world).json()["call_id"]
    task = voice_service.CALLS[call_id].task
    assert task is not None

    async def end() -> None:
        realtime.end.set()
        await task

    assert client.portal is not None
    client.portal.call(end)
    assert call_id not in voice_service.CALLS
    assert len(stored) == 1
    assert hangup(client, world, call_id).status_code == 404


def test_the_time_limit_closes_the_call_and_keeps_the_conversation(
    client: TestClient,
    world: World,
    stored: list[list[ModelMessage]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        interview=SimpleNamespace(
            voice_max_seconds=0.2, voice_attach_timeout_seconds=5.0
        )
    )
    monkeypatch.setattr(voice_service, "get_settings", lambda: settings)
    use(monkeypatch, FakeRealtime())
    call_id = offer(client, world).json()["call_id"]
    task = voice_service.CALLS[call_id].task
    assert task is not None

    async def wait() -> None:
        await asyncio.wait_for(task, 5)

    assert client.portal is not None
    client.portal.call(wait)
    assert call_id not in voice_service.CALLS
    assert len(stored) == 1


def test_a_call_id_of_another_trip_or_unknown_is_404(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, FakeRealtime())
    call_id = offer(client, world).json()["call_id"]
    other = World()
    assert (
        client.post(
            path("voice_hangup", trip_id=other.trip_id, call_id=call_id)
        ).status_code
        == 404
    )
    assert hangup(client, world, "call_unknown").status_code == 404
    assert call_id in voice_service.CALLS  # the other trip did not end it
    assert hangup(client, world, call_id).status_code == 204


def test_a_second_call_on_the_same_interview_is_409(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, FakeRealtime())
    call_id = offer(client, world).json()["call_id"]
    assert offer(client, world).status_code == 409
    hangup(client, world, call_id)


def test_a_sideband_that_cannot_attach_is_503_and_leaves_no_call(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, FakeRealtime(fail_attach=True))
    assert offer(client, world).status_code == 503
    assert not voice_service.CALLS


def test_a_sideband_that_never_attaches_times_out_with_503(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = SimpleNamespace(
        interview=SimpleNamespace(
            voice_max_seconds=60.0, voice_attach_timeout_seconds=0.1
        )
    )
    monkeypatch.setattr(voice_service, "get_settings", lambda: settings)
    use(monkeypatch, FakeRealtime(never_attach=True))
    assert offer(client, world).status_code == 503
    assert not voice_service.CALLS


def test_a_refusing_provider_is_503(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    realtime = FakeRealtime()
    realtime.answer_webrtc_offer = AsyncMock(side_effect=UserError("no key"))
    use(monkeypatch, realtime)
    assert offer(client, world).status_code == 503


def test_access_follows_the_other_interview_routes(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def role(value: TripRole | None) -> AsyncMock:
        def check(_s: object, trip_id: uuid.UUID, sub: str, min_role: TripRole) -> Any:  # ruff: ignore[any-type]
            if value is None:
                raise trip_service.TripNotFoundError(str(trip_id))
            if not value.satisfies(min_role):
                raise trip_service.TripRoleError(str(min_role))
            return TripMembership(trip_id=trip_id, sub=sub, role=value)

        return AsyncMock(side_effect=check)

    use(monkeypatch, FakeRealtime())
    monkeypatch.setattr(trip_service, "get_membership", role(None))
    assert offer(client, world).status_code == 404
    assert hangup(client, world, "c").status_code == 404
    monkeypatch.setattr(trip_service, "get_membership", role(TripRole.MEMBER))
    assert offer(client, world).status_code == 403
    assert hangup(client, world, "c").status_code == 403
    assert not voice_service.CALLS


def test_an_empty_or_huge_offer_is_422(client: TestClient, world: World) -> None:
    assert offer(client, world, "").status_code == 422
    assert offer(client, world, "x" * 200_000).status_code == 422


# --- the agent really resolves in realtime ---------------------------------


class RecordingRealtimeModel(RealtimeModel):
    """A realtime model that only records the configuration it was offered."""

    def __init__(self) -> None:
        super().__init__()
        self.tools: list[str] = []
        self.instructions: str | None = None

    @property
    @override
    def model_name(self) -> str:
        return "recording"

    @property
    @override
    def system(self) -> str:
        return "openai"

    @override
    def connect(
        self,
        *,
        messages: Sequence[ModelMessage],
        model_settings: object,
        model_request_parameters: ModelRequestParameters,
    ) -> Any:
        raise NotImplementedError

    @override
    async def answer_webrtc_offer(
        self,
        sdp_offer: str,
        *,
        instructions: str | None = None,
        tools: Sequence[Any] | None = None,
        model_settings: object = None,
    ) -> WebRTCAnswer:
        self.instructions = instructions
        self.tools = [t.name for t in tools or []]
        return WebRTCAnswer(
            "ANSWER", session=WebRTCSession("openai", session_id="call_x")
        )


def test_the_interview_agent_resolves_its_tools_and_instructions_for_realtime(
    world: World,
) -> None:
    model = RecordingRealtimeModel()
    realtime = voice_service.realtime_for(world.deps(), model)

    async def go() -> None:
        await realtime.answer_webrtc_offer("OFFER")

    asyncio.run(go())
    assert {"set_trip_basics", "add_person", "set_diet", "show_card"} <= set(
        model.tools
    )
    assert model.instructions is not None
    assert "voice call" in model.instructions
    assert interview_agent is not None
