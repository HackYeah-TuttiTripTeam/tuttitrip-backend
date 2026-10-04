"""Voice interview over Pydantic AI Realtime and WebRTC (issue #61).

The browser talks to OpenAI directly (audio never passes through this server).
The server holds the control plane, a *sideband*: it runs the interview agent's
tools for the call, with the membership from ``deps``, and keeps the
transcript. The OpenAI key stays here (``OPENAI_API_KEY``, read by Pydantic AI).

Calls live in a registry in this process (the API is one uvicorn process), so a
deploy drops them. When a call ends (hang-up, time limit, error), its transcript
is appended to the interview session's history and the call leaves the registry.
The host's panel does not get ``STATE_SNAPSHOT`` during a call: the client
re-reads ``GET .../knowledge``.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx
from pydantic_ai import UsageLimits
from pydantic_ai.agent import AgentRealtime
from pydantic_ai.exceptions import ModelAPIError, UserError
from pydantic_ai.messages import ModelMessage, ModelRequest, SystemPromptPart
from pydantic_ai.realtime import RealtimeModel, WebRTCSession
from pydantic_ai.realtime.openai import OpenAIRealtimeModelSettings

from tuttitrip.interview import constants
from tuttitrip.interview.schemas import VoiceAnswer
from tuttitrip.interview.services import history_repair, run_guard, session_service
from tuttitrip.interview.services.interview_agent import interview_agent
from tuttitrip.interview.services.interview_deps import InterviewDeps
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import get_sessionmaker
from tuttitrip.trips.schemas import TripMembership, TripRole

logger = logging.getLogger(__name__)


class VoiceUnavailableError(Exception):
    """The call could not be set up (no key, provider error, sideband not attached)."""


class CallNotFoundError(Exception):
    """No such call on this trip."""


@dataclass
class Call:
    """One live call and its sideband task."""

    deps: InterviewDeps
    provider_session: WebRTCSession
    claim: run_guard.Claim
    task: asyncio.Task[None] | None = None
    attached: asyncio.Event = field(default_factory=asyncio.Event)
    attach_error: BaseException | None = None


CALLS: dict[str, Call] = {}
"""Live calls by provider call id. Empty after a restart."""

_TASKS: set[asyncio.Task[None]] = set()
"""Strong references: the loop keeps only weak ones to tasks."""


def realtime_for(
    deps: InterviewDeps, model: RealtimeModel | None = None
) -> AgentRealtime[InterviewDeps]:
    """Bind the interview agent to the realtime model for one call.

    Args:
        deps: The call's dependencies (membership, session id, database).
        model: Replaces the configured model (tests).

    Returns:
        The agent's realtime accessor.
    """
    interview = get_settings().interview
    return interview_agent.realtime(
        model or interview.voice_model,
        deps=deps,
        model_settings=OpenAIRealtimeModelSettings(input_transcription_model="auto"),
        instructions=constants.VOICE_INSTRUCTIONS,
        usage_limits=UsageLimits(cost_limit=interview.voice_budget_usd),
    )


def without_system_prompts(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Drop system prompts from a transcript: instructions are applied per run.

    Args:
        messages: ``session.all_messages()``.

    Returns:
        The messages without ``SystemPromptPart``s; requests left empty are dropped.
    """
    kept: list[ModelMessage] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            parts = [p for p in message.parts if not isinstance(p, SystemPromptPart)]
            if not parts:
                continue
            message = message.__class__(  # ruff: ignore[redefined-loop-name] same message, fewer parts
                parts=parts,
                instructions=message.instructions,
                metadata=message.metadata,
            )
        kept.append(message)
    return kept


async def _provider_hangup(call_id: str) -> None:
    """End the WebRTC call at OpenAI (best effort: the browser closes it too)."""
    key = os.environ.get(constants.OPENAI_KEY_ENV)
    if not key:
        return
    url = constants.OPENAI_HANGUP_URL.format(call_id=call_id)
    try:
        async with httpx.AsyncClient(
            timeout=constants.HANGUP_TIMEOUT_SECONDS
        ) as client:
            await client.post(url, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError:
        logger.warning("Hangup of call %s failed", call_id, exc_info=True)


async def _store(call: Call, transcript: list[ModelMessage]) -> None:
    messages = history_repair.redact(without_system_prompts(transcript))
    if not messages:
        return
    deps = call.deps
    async with deps.sessions() as session:
        await session_service.append_messages(
            session, deps.membership, deps.session_id, messages
        )


async def _listen(
    call: Call, realtime: AgentRealtime[InterviewDeps], transcript: list[ModelMessage]
) -> None:
    """Attach to the call and let the session run the tools until it ends."""
    async with realtime.session(provider_session=call.provider_session) as session:
        call.attached.set()
        try:
            async for _event in session:
                pass
        finally:
            transcript.extend(session.all_messages())


async def _sideband(call: Call, realtime: AgentRealtime[InterviewDeps]) -> None:
    """Run the agent's tools for the call until it ends, then store the transcript."""
    call_id = call.provider_session.call_id
    transcript: list[ModelMessage] = []
    try:
        async with asyncio.timeout(get_settings().interview.voice_max_seconds):
            await _listen(call, realtime, transcript)
    except TimeoutError:
        logger.info("Call %s reached the time limit", call_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if call.attached.is_set():
            logger.exception("Sideband of call %s failed", call_id)
        else:
            call.attach_error = exc
            call.attached.set()
    finally:
        CALLS.pop(call_id, None)
        try:
            await _store(call, transcript)
        except Exception:
            logger.exception("Could not store the transcript of call %s", call_id)
        await run_guard.release(call.claim, voice=True)
        await _provider_hangup(call_id)


async def answer_offer(membership: TripMembership, sdp: str) -> VoiceAnswer:
    """Answer the browser's offer and attach the server sideband.

    The answer is returned only after the sideband is attached, so the first
    thing the host says already reaches the tools.

    Args:
        membership: The caller's checked membership (co-host or above).
        sdp: The browser's SDP offer.

    Returns:
        The SDP answer and the call id.

    Raises:
        SessionBusyError: The interview has a live call or a running text turn.
        VoiceBudgetError: The trip's voice time is used up.
        VoiceUnavailableError: The provider refused, or the sideband did not
            attach within ``interview.voice_attach_timeout_seconds``.
    """
    sessions = get_sessionmaker()
    async with sessions() as session:
        interview_session, _ = await session_service.open_session(session, membership)
    interview = get_settings().interview
    claim = await run_guard.acquire(
        sessions,
        membership,
        interview_session.id,
        limit_seconds=interview.voice_max_seconds
        + interview.voice_attach_timeout_seconds,
        voice_limit=interview.voice_trip_seconds,
    )
    deps = InterviewDeps(
        membership=membership,
        session_id=interview_session.id,
        sessions=sessions,
        voice=True,
    )
    realtime = realtime_for(deps)
    try:
        answer = await realtime.answer_webrtc_offer(sdp)
    except (UserError, ModelAPIError, httpx.HTTPError) as exc:
        await run_guard.release(claim)
        logger.warning("The provider refused the offer", exc_info=exc)
        msg = "The voice service is not available"
        raise VoiceUnavailableError(msg) from exc
    except BaseException:
        await run_guard.release(claim)
        raise
    call = Call(deps=deps, provider_session=answer.session, claim=claim)
    call_id = answer.session.call_id
    CALLS[call_id] = call
    call.task = asyncio.create_task(_sideband(call, realtime))
    _TASKS.add(call.task)
    call.task.add_done_callback(_TASKS.discard)
    try:
        async with asyncio.timeout(
            get_settings().interview.voice_attach_timeout_seconds
        ):
            await call.attached.wait()
    except TimeoutError as exc:
        await _end(call)
        msg = "The assistant did not join the call in time"
        raise VoiceUnavailableError(msg) from exc
    if call.attach_error is not None:
        raise VoiceUnavailableError(str(call.attach_error)) from call.attach_error
    return VoiceAnswer(sdp=answer.sdp, call_id=call_id)


async def _end(call: Call) -> None:
    task = call.task
    if task is None:
        return
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def hang_up(membership: TripMembership, call_id: str) -> None:
    """End a call of this trip and store its transcript before returning.

    Args:
        membership: The caller's checked membership.
        call_id: The id returned by the offer.

    Raises:
        CallNotFoundError: No such live call on this trip (also for another
            trip's call, so ids do not leak), or the caller neither started
            it nor is the trip's host.
    """
    call = CALLS.get(call_id)
    if call is None or call.deps.membership.trip_id != membership.trip_id:
        raise CallNotFoundError(call_id)
    owner = call.deps.membership.sub == membership.sub
    if not owner and not membership.role.satisfies(TripRole.HOST):
        raise CallNotFoundError(call_id)
    await _end(call)
