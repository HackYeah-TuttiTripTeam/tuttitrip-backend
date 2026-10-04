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

import httpx
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
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.realtime import (
    RealtimeModel,
    WebRTCAnswer,
    WebRTCSession,
)

from tests.shared.fakes import authorize
from tests.shared.interview_world import World
from tests.shared.paths import path
from tuttitrip.interview import constants
from tuttitrip.interview.schemas import (
    CardKind,
    QuestionField,
    SessionStatus,
    ShownCard,
)
from tuttitrip.interview.services import run_guard, session_service, voice_service
from tuttitrip.interview.services.interview_agent import interview_agent
from tuttitrip.main import create_app
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

pytestmark = [
    pytest.mark.usefixtures("stored"),
    pytest.mark.filterwarnings(
        "ignore::pydantic_ai_harness.spend.UnpricedModelWarning"
    ),
]

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
    w.known_sessions = {SESSION_ID}
    monkeypatch.setattr(voice_service, "get_sessionmaker", lambda: w.deps().sessions)
    monkeypatch.setattr(voice_service, "_provider_hangup", AsyncMock())
    monkeypatch.setattr(voice_service, "_extract", AsyncMock())
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
    monkeypatch.setattr(
        voice_service, "realtime_for", lambda _deps, locale="pl": (locale, realtime)[1]
    )


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
            voice_max_seconds=0.2,
            voice_attach_timeout_seconds=5.0,
            voice_trip_seconds=1800,
            voice_heartbeat_seconds=10.0,
            voice_claim_ttl_seconds=45.0,
            run_timeout_seconds=5.0,
        )
    )
    monkeypatch.setattr(voice_service, "get_settings", lambda: settings)
    monkeypatch.setattr(run_guard, "get_settings", lambda: settings)
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
    assert not world.running


def test_voice_and_text_exclude_each_other(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, FakeRealtime())
    text_turn = asyncio.run(
        run_guard.acquire(
            world.deps().sessions, world.membership, SESSION_ID, limit_seconds=60
        )
    )
    assert offer(client, world).status_code == 409  # a text turn is running
    asyncio.run(run_guard.release(text_turn))
    call_id = offer(client, world).json()["call_id"]
    with pytest.raises(run_guard.SessionBusyError):  # a call is running
        asyncio.run(
            run_guard.acquire(
                world.deps().sessions, world.membership, SESSION_ID, limit_seconds=60
            )
        )
    hangup(client, world, call_id)


def test_two_offers_at_once_get_one_call(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    realtime = FakeRealtime()
    use(monkeypatch, realtime)

    async def both() -> list[int]:
        app: Any = client.app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as http:
            url = path("voice_offer", trip_id=world.trip_id)
            responses = await asyncio.gather(
                http.post(url, json={"sdp": "OFFER"}),
                http.post(url, json={"sdp": "OFFER"}),
            )
        return sorted(r.status_code for r in responses)

    assert client.portal is not None
    assert client.portal.call(both) == [200, 409]
    assert realtime.calls == 1  # the loser never reached the provider
    for call_id in list(voice_service.CALLS):
        hangup(client, world, call_id)


def test_the_trips_voice_time_is_limited_and_booked_per_call(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = SimpleNamespace(
        interview=SimpleNamespace(
            voice_max_seconds=60.0,
            voice_attach_timeout_seconds=5.0,
            voice_trip_seconds=3,
            voice_heartbeat_seconds=10.0,
            voice_claim_ttl_seconds=45.0,
            run_timeout_seconds=5.0,
        )
    )
    monkeypatch.setattr(voice_service, "get_settings", lambda: settings)
    monkeypatch.setattr(run_guard, "get_settings", lambda: settings)
    use(monkeypatch, FakeRealtime())
    first = offer(client, world).json()["call_id"]
    assert hangup(client, world, first).status_code == 204
    assert world.voice_used[SESSION_ID] >= 1  # the call's time is booked
    world.voice_used[SESSION_ID] = 3  # the limit is reached
    refused = offer(client, world)
    assert refused.status_code == 429
    assert not voice_service.CALLS
    assert not world.running


def test_only_the_caller_who_started_the_call_or_the_host_may_end_it(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, FakeRealtime())
    call_id = offer(client, world).json()["call_id"]

    def as_role(sub: str, role: TripRole) -> None:
        monkeypatch.setattr(
            trip_service,
            "get_membership",
            AsyncMock(
                return_value=TripMembership(trip_id=world.trip_id, sub=sub, role=role)
            ),
        )

    as_role("auth0|other-cohost", TripRole.CO_HOST)
    assert hangup(client, world, call_id).status_code == 404
    assert call_id in voice_service.CALLS
    as_role("auth0|other-host", TripRole.HOST)
    assert hangup(client, world, call_id).status_code == 204


def test_the_stored_transcript_is_redacted_like_the_text_interview(
    client: TestClient,
    world: World,
    stored: list[list[ModelMessage]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "AKIAIOSFODNN7EXAMPLE"  # the documented AWS example key
    leaked = [ModelRequest(parts=[UserPromptPart(content=f"klucz {secret}")])]
    monkeypatch.setattr(
        FakeProviderSession, "all_messages", staticmethod(lambda: leaked)
    )
    use(monkeypatch, FakeRealtime())
    call_id = offer(client, world).json()["call_id"]
    hangup(client, world, call_id)
    (transcript,) = stored
    text = str(transcript)
    assert secret not in text
    assert "REDACTED" in text.upper()


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
            voice_max_seconds=60.0,
            voice_attach_timeout_seconds=0.1,
            voice_trip_seconds=1800,
            voice_heartbeat_seconds=10.0,
            voice_claim_ttl_seconds=45.0,
            run_timeout_seconds=5.0,
        )
    )
    monkeypatch.setattr(voice_service, "get_settings", lambda: settings)
    monkeypatch.setattr(run_guard, "get_settings", lambda: settings)
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
    deps = world.deps()
    deps.voice = True
    realtime = voice_service.realtime_for(deps, model)

    async def go() -> None:
        await realtime.answer_webrtc_offer("OFFER")

    asyncio.run(go())
    assert {"set_trip_basics", "add_person", "set_diet"} <= set(model.tools)
    assert "show_card" in model.tools  # the card appears next to the captions
    assert model.instructions is not None
    assert "voice call" in model.instructions
    assert "Do not use show_card" not in model.instructions
    assert "show_card" in model.instructions


# --- #214: stale locks, takeover, language, extraction ---------------------


def tuned(monkeypatch: pytest.MonkeyPatch, **values: float) -> None:
    """Settings of a test: short claims and a fast heartbeat."""
    settings = SimpleNamespace(
        interview=SimpleNamespace(
            **{
                "voice_max_seconds": 60.0,
                "voice_attach_timeout_seconds": 5.0,
                "voice_trip_seconds": 1800,
                "voice_heartbeat_seconds": 10.0,
                "voice_claim_ttl_seconds": 45.0,
                "run_timeout_seconds": 5.0,
                **values,
            }
        )
    )
    monkeypatch.setattr(voice_service, "get_settings", lambda: settings)
    monkeypatch.setattr(run_guard, "get_settings", lambda: settings)


def acquire_text(world: World) -> Any:  # ruff: ignore[any-type]
    return asyncio.run(
        run_guard.acquire(
            world.deps().sessions, world.membership, SESSION_ID, limit_seconds=60
        )
    )


def test_a_dead_calls_claim_expires_on_its_own_and_a_text_turn_takes_over(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    tuned(monkeypatch, voice_claim_ttl_seconds=0.1)

    async def scenario() -> None:
        sessions = world.deps().sessions
        await run_guard.acquire(
            sessions,
            world.membership,
            SESSION_ID,
            limit_seconds=60,
            voice_limit=1800,
        )  # a call whose tab vanished: nobody releases it or sends a heartbeat
        with pytest.raises(run_guard.SessionBusyError) as busy:
            await run_guard.acquire(
                sessions, world.membership, SESSION_ID, limit_seconds=60
            )
        assert busy.value.kind == "voice"
        await asyncio.sleep(0.2)
        turn = await run_guard.acquire(
            sessions, world.membership, SESSION_ID, limit_seconds=60
        )
        await run_guard.release(turn)

    asyncio.run(scenario())
    assert not world.running


def test_the_heartbeat_keeps_a_live_call_claimed_and_stops_with_the_call(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    tuned(monkeypatch, voice_claim_ttl_seconds=0.3, voice_heartbeat_seconds=0.05)

    async def scenario() -> None:
        sessions = world.deps().sessions
        claim = await run_guard.acquire(
            sessions,
            world.membership,
            SESSION_ID,
            limit_seconds=60,
            voice_limit=1800,
        )
        owner = asyncio.current_task()
        assert owner is not None
        call = voice_service.Call(
            deps=world.deps(),
            provider_session=WebRTCSession("openai", session_id="c"),
            claim=claim,
        )
        beat = asyncio.create_task(voice_service._heartbeat(call, owner))  # ruff: ignore[private-member-access]
        await asyncio.sleep(0.8)  # longer than the claim lives without a beat
        with pytest.raises(run_guard.SessionBusyError):
            await run_guard.acquire(
                sessions, world.membership, SESSION_ID, limit_seconds=60
            )
        beat.cancel()
        await asyncio.sleep(0.5)  # the beat stopped: the claim lapses
        turn = await run_guard.acquire(
            sessions, world.membership, SESSION_ID, limit_seconds=60
        )
        await run_guard.release(turn)

    asyncio.run(scenario())


def test_the_409_says_what_holds_the_interview(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, FakeRealtime())
    text_turn = acquire_text(world)
    assert (
        offer(client, world).json()["detail"]
        == "Another turn of this interview is still running"
    )
    asyncio.run(run_guard.release(text_turn))
    call_id = offer(client, world).json()["call_id"]
    assert (
        offer(client, world).json()["detail"]
        == "A voice call of this interview is still running"
    )
    hangup(client, world, call_id)


def release(client: TestClient, world: World) -> Any:  # ruff: ignore[any-type]
    return client.post(path("voice_release", trip_id=world.trip_id))


def test_release_ends_the_call_stores_it_and_frees_the_interview(
    client: TestClient,
    world: World,
    stored: list[list[ModelMessage]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use(monkeypatch, FakeRealtime())
    call_id = offer(client, world).json()["call_id"]
    assert release(client, world).status_code == 204
    assert call_id not in voice_service.CALLS
    assert len(stored) == 1  # the transcript is not lost
    assert not world.running
    # A text turn takes the interview over at once.
    asyncio.run(run_guard.release(acquire_text(world)))
    assert release(client, world).status_code == 204  # nothing runs: still fine


def test_release_clears_a_claim_left_by_a_call_this_process_does_not_know(
    client: TestClient, world: World
) -> None:
    asyncio.run(
        run_guard.acquire(
            world.deps().sessions,
            world.membership,
            SESSION_ID,
            limit_seconds=60,
            voice_limit=1800,
        )
    )  # after a restart or on another process: no entry in CALLS
    assert offer(client, world).status_code == 409
    assert release(client, world).status_code == 204
    assert not world.running


def test_release_leaves_a_running_text_turn_alone(
    client: TestClient, world: World
) -> None:
    turn = acquire_text(world)
    assert release(client, world).status_code == 204
    assert world.running  # still held by the text turn
    asyncio.run(run_guard.release(turn))


def test_a_call_whose_claim_was_taken_away_ends_by_itself(
    client: TestClient,
    world: World,
    stored: list[list[ModelMessage]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tuned(monkeypatch, voice_heartbeat_seconds=0.05)
    use(monkeypatch, FakeRealtime())
    call_id = offer(client, world).json()["call_id"]
    task = voice_service.CALLS[call_id].task
    assert task is not None
    world.running.clear()  # cleared from outside, e.g. by a release on another process

    async def wait() -> None:
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)

    assert client.portal is not None
    client.portal.call(wait)
    assert call_id not in voice_service.CALLS
    assert len(stored) == 1


def test_release_needs_the_interview_permission(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def check(_s: object, trip_id: uuid.UUID, sub: str, min_role: TripRole) -> Any:  # ruff: ignore[any-type]
        if not TripRole.MEMBER.satisfies(min_role):
            raise trip_service.TripRoleError(str(min_role))
        return TripMembership(trip_id=trip_id, sub=sub, role=TripRole.MEMBER)

    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=check))
    assert release(client, world).status_code == 403


def test_the_call_language_is_pinned_in_the_transcription_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for locale in ("pl", "en"):
        model = voice_service.LocalizedRealtimeModel(
            "gpt-realtime-2.1-mini", language=locale
        )
        config = model._session_config("hi", None, model_settings=None)  # ruff: ignore[private-member-access]
        assert config["audio"]["input"]["transcription"]["language"] == locale


def test_the_instructions_name_the_language_the_currency_rule_and_the_tool_rules(
    world: World,
) -> None:
    model = RecordingRealtimeModel()
    deps = world.deps()
    deps.voice = True
    realtime = voice_service.realtime_for(deps, model, locale="en")

    async def go() -> None:
        await realtime.answer_webrtc_offer("OFFER")

    asyncio.run(go())
    text = model.instructions or ""
    assert "English" in text
    assert "Currency: PLN" in text  # the trip has none, so the default
    assert "processing" in text  # the rule that forbids saying it
    assert "build_plan_now" in text


def test_the_trips_own_currency_is_what_the_context_says(world: World) -> None:
    world.trip = world.trip.model_copy(update={"currency": "EUR"})
    model = RecordingRealtimeModel()
    deps = world.deps()
    deps.voice = True
    realtime = voice_service.realtime_for(deps, model)

    async def go() -> None:
        await realtime.answer_webrtc_offer("OFFER")

    asyncio.run(go())
    assert "Currency: EUR" in (model.instructions or "")


def test_the_extraction_run_saves_the_calls_facts_and_stores_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.undo()  # the real _extract, on the world's fake database
    world.install(monkeypatch)
    monkeypatch.setattr(
        session_service, "load_history", AsyncMock(return_value=list(TRANSCRIPT))
    )
    seen: list[str] = []
    moves = iter(
        [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "set_trip_basics",
                        {"city": "Berlin", "start_date": "2026-10-16", "days": 3},
                    )
                ]
            ),
            ModelResponse(parts=[TextPart("Zapisane.")]),
        ]
    )

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        seen.append(str(messages[-1]))
        return next(moves)

    appended = AsyncMock()
    monkeypatch.setattr(session_service, "append_messages", appended)
    call = voice_service.Call(
        deps=world.deps(),
        provider_session=WebRTCSession("openai", session_id="c"),
        claim=SimpleNamespace(),  # ty: ignore[invalid-argument-type] not used by the extraction
    )
    with interview_agent.override(model=FunctionModel(respond)):
        asyncio.run(voice_service._extract(call))  # ruff: ignore[private-member-access]
    assert world.trip.destination == "Berlin"
    assert constants.EXTRACTION_PROMPT in seen[0]
    appended.assert_not_awaited()  # the host sees no prompt of ours in the history


def test_a_failing_extraction_does_not_lose_the_stored_transcript(
    client: TestClient,
    world: World,
    stored: list[list[ModelMessage]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        voice_service, "_extract", AsyncMock(side_effect=RuntimeError("model down"))
    )
    use(monkeypatch, FakeRealtime())
    call_id = offer(client, world).json()["call_id"]
    assert hangup(client, world, call_id).status_code == 204
    assert len(stored) == 1
    assert not world.running  # the interview is free again
    voice_service._extract.assert_awaited_once()  # ty: ignore[unresolved-attribute]  # ruff: ignore[private-member-access]


def test_a_call_without_a_transcript_runs_no_extraction(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeProviderSession, "all_messages", staticmethod(list))
    use(monkeypatch, FakeRealtime())
    call_id = offer(client, world).json()["call_id"]
    hangup(client, world, call_id)
    voice_service._extract.assert_not_awaited()  # ty: ignore[unresolved-attribute]  # ruff: ignore[private-member-access]


# --- backend#220: the card of a live call -----------------------------------


def card_of(client: TestClient, world: World, call_id: str) -> Any:  # ruff: ignore[any-type]
    return client.get(path("voice_card", trip_id=world.trip_id, call_id=call_id))


def test_a_live_call_shows_the_card_the_assistant_put_on_screen(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, FakeRealtime())
    call_id = offer(client, world).json()["call_id"]
    assert card_of(client, world, call_id).json() == {"card": None}
    voice_service.CALLS[call_id].deps.state.card = ShownCard(
        kind=CardKind.CITY,
        question="Dokąd jedziecie?",
        field=QuestionField.DESTINATION,
    )
    response = card_of(client, world, call_id)
    assert response.status_code == 200
    assert response.json()["card"] == {
        "kind": "city",
        "question": "Dokąd jedziecie?",
        "field": "destination",
        "person_id": None,
        "options": [],
    }


def test_the_card_of_an_unknown_call_or_another_trip_is_404(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, FakeRealtime())
    call_id = offer(client, world).json()["call_id"]
    assert card_of(client, world, "call_unknown").status_code == 404
    other = World()
    assert (
        client.get(
            path("voice_card", trip_id=other.trip_id, call_id=call_id)
        ).status_code
        == 404
    )
