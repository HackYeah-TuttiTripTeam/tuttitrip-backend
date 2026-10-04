"""The AG-UI endpoint of the text interview.

The protocol is handled by Pydantic AI's ``AGUIAdapter``; this module makes it
trust only what the server knows:

- The only client input is the text of its last user message. It is the
  ``user_prompt`` of the run; the stored history is the ``message_history``.
  The client's other messages, ``state``, ``tools`` and ``resume`` are ignored,
  so a client cannot forge history, system prompts, tool results or deps.
- ``result.new_messages()`` is appended to the stored history when the run
  completes, before ``RUN_FINISHED``.
- One run per session at a time (``run_guard``), at most
  ``interview.run_timeout_seconds`` long.
- Run errors reach the client as a Polish ``RUN_ERROR`` with a stable ``code``.

The events are those of the AG-UI 1.0 specification, produced by the adapter:
``RUN_STARTED``, ``TEXT_MESSAGE_*``, ``TOOL_CALL_*``, ``STATE_SNAPSHOT`` (from
the tools), ``RUN_FINISHED`` or ``RUN_ERROR``. It does not send ``STATE_DELTA``
or ``ACTIVITY_*``; ``REASONING_*`` only when the model returns thinking parts.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import KW_ONLY, dataclass
from typing import Any, override
from uuid import UUID

import anyio
import httpx
from ag_ui.core import BaseEvent, RunAgentInput, RunErrorEvent
from pydantic import ValidationError
from pydantic_ai import capture_run_messages
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.exceptions import (
    FallbackExceptionGroup,
    ModelAPIError,
    UsageLimitExceeded,
    UserError,
)
from pydantic_ai.messages import ModelMessage
from pydantic_ai.ui import NativeEvent
from pydantic_ai.ui.ag_ui import AGUIAdapter, AGUIEventStream
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.interview import constants
from tuttitrip.interview.services import history_repair, run_guard, session_service
from tuttitrip.interview.services.interview_agent import interview_agent
from tuttitrip.interview.services.interview_deps import InterviewDeps
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import get_sessionmaker
from tuttitrip.trips.schemas import TripMembership

logger = logging.getLogger(__name__)

USER_ROLE = "user"


class PromptError(Exception):
    """The request carries no usable user text."""


@dataclass(frozen=True)
class RunRequest:
    """What is taken from a client request: the rest of it is ignored."""

    run_input: RunAgentInput
    thread_id: UUID
    text: str


def read_request(body: bytes) -> RunRequest:
    """Take the thread id and the last user text from an AG-UI request body.

    Args:
        body: The raw JSON body of ``RunAgentInput``.

    Returns:
        The input, the session id (``threadId``) and the host's text.

    Raises:
        PromptError: The body is not a valid ``RunAgentInput``, ``threadId`` is
            not a session id, or there is no usable user text.
    """
    try:
        run_input = AGUIAdapter.build_run_input(body)
    except ValidationError as exc:
        msg = "The body is not a valid AG-UI RunAgentInput"
        raise PromptError(msg) from exc
    try:
        thread_id = UUID(run_input.thread_id)
    except ValueError as exc:
        msg = "threadId must be the id of the interview session"
        raise PromptError(msg) from exc
    last = run_input.messages[-1] if run_input.messages else None
    if last is None or last.role != USER_ROLE:
        msg = "The last message must be the host's own text"
        raise PromptError(msg)
    content: Any = last.content
    text = (
        content
        if isinstance(content, str)
        else " ".join(getattr(part, "text", "") for part in content)
    ).strip()
    if not text or len(text) > constants.MAX_USER_TEXT:
        msg = f"The text must have 1 to {constants.MAX_USER_TEXT} characters"
        raise PromptError(msg)
    return RunRequest(run_input=run_input, thread_id=thread_id, text=text)


def new_deps(
    membership: TripMembership,
    session_id: UUID,
    own_profile_id: UUID | None = None,
) -> InterviewDeps:
    """Dependencies of one turn: the checked caller and a way to the database.

    Args:
        membership: The caller's checked membership.
        session_id: The interview session (``threadId``).
        own_profile_id: A member's own profile (their interview); None for the
            trip's interview.

    Returns:
        Fresh deps with an empty state.
    """
    return InterviewDeps(
        membership=membership,
        session_id=session_id,
        sessions=get_sessionmaker(),
        own_profile_id=own_profile_id,
    )


UNAVAILABLE_ERRORS = (UserError, ModelAPIError, FallbackExceptionGroup, httpx.HTTPError)
"""Failures to reach a model: nothing the host did wrong, try again later."""


def run_error(error: BaseException) -> tuple[str, str]:
    """Turn a failed run into the client's message and code.

    Args:
        error: What the run raised.

    Returns:
        A Polish message and a stable code; the details go to the log.
    """
    logger.warning("Interview run failed", exc_info=error)
    if isinstance(error, UsageLimitExceeded):  # also SpendLimitExceeded
        return constants.ERROR_SPEND_PL, constants.ERROR_CODE_SPEND
    if isinstance(error, TimeoutError):
        return constants.ERROR_TIMEOUT_PL, constants.ERROR_CODE_TIMEOUT
    if isinstance(error, UNAVAILABLE_ERRORS):
        return constants.ERROR_UNAVAILABLE_PL, constants.ERROR_CODE_UNAVAILABLE
    return constants.ERROR_GENERIC_PL, constants.ERROR_CODE_GENERIC


class InterviewEventStream(AGUIEventStream[InterviewDeps, str]):
    """AG-UI events with run errors in Polish and with a code."""

    @override
    async def on_error(self, error: Exception) -> AsyncIterator[BaseEvent]:
        message, code = run_error(error)
        async for event in super().on_error(Exception(message)):
            if isinstance(event, RunErrorEvent):
                event = event.model_copy(update={"code": code})  # ruff: ignore[redefined-loop-name] only the code is added
            yield event


@dataclass
class InterviewAdapter(AGUIAdapter[InterviewDeps, str]):
    """AG-UI adapter whose run uses the server's history and the host's last text."""

    _: KW_ONLY
    text: str = ""
    deps: InterviewDeps | None = None
    claim: run_guard.Claim | None = None
    completed: bool = False

    @override
    def build_event_stream(self) -> InterviewEventStream:
        return InterviewEventStream(self.run_input, accept=self.accept)

    async def _keep_partial(self, captured: list[ModelMessage], skip: int) -> None:
        """Store what a failed turn did, so a retry does not repeat tool calls."""
        deps = self.deps
        messages = history_repair.settle(captured[skip:])
        if deps is None or not messages:
            return
        with anyio.CancelScope(shield=True):
            try:
                async with deps.sessions() as session:
                    await session_service.append_messages(
                        session, deps.membership, deps.session_id, messages
                    )
            except Exception:
                logger.exception("Could not store the partial turn")

    @override
    def run_stream_native(self, **kwargs: Any) -> AsyncIterator[NativeEvent]:
        timeout = get_settings().interview.run_timeout_seconds
        skip = len(kwargs.get("message_history") or [])

        async def stream() -> AsyncIterator[NativeEvent]:
            try:
                with capture_run_messages() as captured:
                    try:
                        async with (
                            asyncio.timeout(timeout),
                            self.agent.run_stream_events(self.text, **kwargs) as events,
                        ):
                            async for event in events:
                                yield event  # ruff: ignore[yield-in-context-manager-in-async-generator] as in the adapter's own stream
                    finally:
                        if not self.completed:
                            await self._keep_partial(captured, skip)
            finally:
                if self.claim is not None:
                    await run_guard.release(self.claim)

        return stream()


@dataclass
class InterviewStream:
    """What the endpoint sends: encoded events and how to label them."""

    body: AsyncIterator[str]
    media_type: str
    headers: Mapping[str, str] | None
    claim: run_guard.Claim


def start(
    request: RunRequest,
    accept: str | None,
    deps: InterviewDeps,
    history: Sequence[ModelMessage],
    claim: run_guard.Claim,
) -> InterviewStream:
    """Prepare one turn: the run is started when the client reads the stream.

    Args:
        request: The parsed request; only its last user text is used.
        accept: The ``Accept`` header of the request.
        deps: The turn's dependencies (membership, session id, sessions).
        history: The stored history of the session (``load_history``).
        claim: The session claim; the stream gives it back when it ends.

    Returns:
        The encoded SSE stream.
    """
    adapter = InterviewAdapter(
        agent=interview_agent,
        run_input=request.run_input,
        accept=accept,
        text=request.text,
        deps=deps,
        claim=claim,
    )

    async def store(result: AgentRunResult[str]) -> None:
        adapter.completed = True
        async with deps.sessions() as session:
            await session_service.append_messages(
                session, deps.membership, deps.session_id, result.new_messages()
            )

    events = adapter.run_stream(
        message_history=history,
        deps=deps,
        conversation_id=str(deps.session_id),
        on_complete=store,
    )
    stream = adapter.build_event_stream()
    return InterviewStream(
        body=adapter.encode_stream(events),
        media_type=stream.content_type,
        headers=stream.response_headers,
        claim=claim,
    )


async def begin(
    request: RunRequest,
    accept: str | None,
    membership: TripMembership,
    session: AsyncSession,
) -> InterviewStream:
    """Claim the session, read its history and prepare the turn.

    The claim comes first, so two turns can never both read the same history.
    It is given back on any failure here; afterwards the stream (and the
    response's background task) gives it back.

    Args:
        request: The parsed request.
        accept: The ``Accept`` header of the request.
        membership: The caller's checked membership.
        session: The request's database session (for the history).

    Returns:
        The encoded SSE stream.

    Raises:
        SessionNotFoundError: No such session of the caller on this trip.
        NoProfileError: A member without a profile on this trip.
        HistoryIncompatibleError: The stored history cannot be read.
        SessionBusyError: Another run holds the session.
    """
    # Whose session it is is checked before the claim: a member cannot hold, or
    # read, the host's session by naming its id.
    owner = await session_service.check_owned(session, membership, request.thread_id)
    deps = new_deps(membership, request.thread_id, owner)
    claim = await run_guard.acquire(
        deps.sessions,
        membership,
        request.thread_id,
        limit_seconds=get_settings().interview.run_timeout_seconds,
    )
    try:
        history = await session_service.load_history(
            session, membership, request.thread_id
        )
        return start(request, accept, deps, history, claim)
    except BaseException:
        await run_guard.release(claim)
        raise
