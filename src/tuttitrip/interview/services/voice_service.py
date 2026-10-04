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
from typing import Any, override

import httpx
from pydantic_ai import UsageLimits
from pydantic_ai.agent import AgentRealtime
from pydantic_ai.exceptions import ModelAPIError, UserError
from pydantic_ai.messages import ModelMessage, ModelRequest, SystemPromptPart
from pydantic_ai.realtime import RealtimeModel, WebRTCSession
from pydantic_ai.realtime.model import infer_realtime_model
from pydantic_ai.realtime.openai import (
    OpenAIRealtimeModel,
    OpenAIRealtimeModelSettings,
)

from tuttitrip.interview import constants, db
from tuttitrip.interview.schemas import ShownCard, VoiceAnswer
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


class LocalizedRealtimeModel(OpenAIRealtimeModel):
    """OpenAI realtime model whose input transcription is pinned to one language.

    Without it the transcription model guesses the language of every utterance
    and writes a noisy word as French or Chinese. Pydantic AI has no setting for
    it, so the session config is patched where it is built.
    """

    def __init__(self, model_name: str, *, language: str) -> None:
        """Pin the transcription language.

        Args:
            model_name: The OpenAI realtime model name (without the provider).
            language: ISO 639-1 code, ``pl`` or ``en``.
        """
        super().__init__(model_name)
        self._language = language

    @override
    def _session_config(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        config = super()._session_config(*args, **kwargs)
        transcription = config["audio"]["input"].get("transcription")
        if transcription is not None:
            transcription["language"] = self._language
        return config


def voice_model(locale: str) -> RealtimeModel:
    """The configured realtime model with the transcription language of a call.

    Args:
        locale: ``pl`` or ``en``.

    Returns:
        The model; a provider other than OpenAI keeps its own defaults.
    """
    name = get_settings().interview.voice_model
    provider, _, model_name = name.partition(":")
    if provider == "openai" and model_name:
        return LocalizedRealtimeModel(model_name, language=locale)
    return infer_realtime_model(name)


def realtime_for(
    deps: InterviewDeps, model: RealtimeModel | None = None, locale: str = "pl"
) -> AgentRealtime[InterviewDeps]:
    """Bind the interview agent to the realtime model for one call.

    Args:
        deps: The call's dependencies (membership, session id, database).
        model: Replaces the configured model (tests).
        locale: Language of the call, ``pl`` or ``en``.

    Returns:
        The agent's realtime accessor.
    """
    interview = get_settings().interview
    language = constants.VOICE_LANGUAGES.get(locale, constants.VOICE_LANGUAGES["pl"])
    return interview_agent.realtime(
        model or voice_model(locale),
        deps=deps,
        model_settings=OpenAIRealtimeModelSettings(
            input_transcription_model="auto",
            # False = effort "none"; ignored by models without reasoning.
            thinking=interview.voice_reasoning_effort or False,
        ),
        instructions=constants.VOICE_INSTRUCTIONS.format(language=language),
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


async def _store(call: Call, transcript: list[ModelMessage]) -> bool:
    """Append the call's transcript to the session.

    Args:
        call: The call that ended.
        transcript: Its messages.

    Returns:
        False when there was nothing to store.
    """
    messages = history_repair.redact(without_system_prompts(transcript))
    if not messages:
        return False
    deps = call.deps
    async with deps.sessions() as session:
        await session_service.append_messages(
            session, deps.membership, deps.session_id, messages
        )
    return True


async def _extract(call: Call) -> None:
    """Fill in what the call left unsaved, from its transcript.

    One run of the text interview agent with the same tools over the stored
    history. It is safe to repeat: the tools write the same values again, the
    host's own values are protected, and the instructions list the people already
    added. Its messages are not stored, so the host sees no prompt of ours.
    """
    deps = call.deps
    run_deps = InterviewDeps(
        membership=deps.membership,
        session_id=deps.session_id,
        sessions=deps.sessions,
        voice=True,
    )
    async with deps.sessions() as session:
        history = await session_service.load_history(
            session, deps.membership, deps.session_id
        )
    async with asyncio.timeout(get_settings().interview.run_timeout_seconds):
        await interview_agent.run(
            constants.EXTRACTION_PROMPT, message_history=history, deps=run_deps
        )


async def _heartbeat(call: Call, owner: asyncio.Task[None]) -> None:
    """Keep the session claimed while the call lives; stop the call if it is lost.

    A claim that is gone (the host ended the call from another device, or it
    expired) ends the call too.
    """
    interval = get_settings().interview.voice_heartbeat_seconds
    while True:
        await asyncio.sleep(interval)
        try:
            alive = await run_guard.touch(call.claim)
        except Exception:
            logger.warning("Heartbeat of a call failed", exc_info=True)
            continue
        if not alive:
            owner.cancel()
            return


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


async def _finish(call: Call, transcript: list[ModelMessage]) -> None:
    """Store the transcript, then fill in the fields it settles (best effort)."""
    call_id = call.provider_session.call_id
    try:
        stored = await _store(call, transcript)
    except Exception:
        logger.exception("Could not store the transcript of call %s", call_id)
        return
    if not stored:
        return
    try:
        await _extract(call)
    except Exception:
        logger.exception("Could not fill in the fields of call %s", call_id)


async def _sideband(call: Call, realtime: AgentRealtime[InterviewDeps]) -> None:
    """Run the agent's tools for the call until it ends, then store the transcript."""
    call_id = call.provider_session.call_id
    transcript: list[ModelMessage] = []
    beat = asyncio.create_task(_heartbeat(call, asyncio.current_task()))  # ty: ignore[invalid-argument-type] a task runs inside a task
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
        beat.cancel()
        CALLS.pop(call_id, None)
        await _finish(call, transcript)
        await run_guard.release(call.claim, voice=True)
        await _provider_hangup(call_id)


async def answer_offer(
    membership: TripMembership, sdp: str, locale: str = "pl"
) -> VoiceAnswer:
    """Answer the browser's offer and attach the server sideband.

    The answer is returned only after the sideband is attached, so the first
    thing the host says already reaches the tools.

    Args:
        membership: The caller's checked membership (co-host or above).
        sdp: The browser's SDP offer.
        locale: Language of the call, ``pl`` or ``en``.

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
    realtime = realtime_for(deps, locale=locale)
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


def shown_card(membership: TripMembership, call_id: str) -> ShownCard | None:
    """The card the assistant last put on screen in a live call.

    Args:
        membership: The caller's checked membership.
        call_id: The id returned by the offer.

    Returns:
        The card, or None before the first one.

    Raises:
        CallNotFoundError: No such live call on this trip (also for another
            trip's call, so ids do not leak).
    """
    call = CALLS.get(call_id)
    if call is None or call.deps.membership.trip_id != membership.trip_id:
        raise CallNotFoundError(call_id)
    return call.deps.state.card


async def release(membership: TripMembership) -> None:
    """End the voice call of the trip's interview, wherever it runs.

    A call of this process is ended properly (transcript stored, fields filled
    in). A claim left by a call this process does not know (another process, a
    restart) is cleared in the database. A text turn is left alone. Safe to
    call when nothing runs.

    Args:
        membership: The caller's checked membership (co-host or above).

    Raises:
        SessionNotFoundError: The trip has no open interview.
    """
    sessions = get_sessionmaker()
    async with sessions() as session:
        row = await db.select_open_session(session, membership.trip_id)
    if row is None:
        raise session_service.SessionNotFoundError(str(membership.trip_id))
    for call in [c for c in CALLS.values() if c.deps.session_id == row.id]:
        await _end(call)
    await run_guard.release_voice(sessions, membership, row.id)
