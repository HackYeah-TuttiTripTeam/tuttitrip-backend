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

from ag_ui.core import BaseEvent, RunAgentInput, RunErrorEvent
from pydantic import ValidationError
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.exceptions import UsageLimitExceeded, UserError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.ui import NativeEvent
from pydantic_ai.ui.ag_ui import AGUIAdapter, AGUIEventStream

from tuttitrip.interview import constants
from tuttitrip.interview.services import run_guard, session_service
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


def new_deps(membership: TripMembership, session_id: UUID) -> InterviewDeps:
    """Dependencies of one turn: the checked caller and a way to the database.

    Args:
        membership: The caller's checked membership.
        session_id: The interview session (``threadId``).

    Returns:
        Fresh deps with an empty state.
    """
    return InterviewDeps(
        membership=membership, session_id=session_id, sessions=get_sessionmaker()
    )


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
    if isinstance(error, UserError):
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

    @override
    def build_event_stream(self) -> InterviewEventStream:
        return InterviewEventStream(self.run_input, accept=self.accept)

    @override
    def run_stream_native(self, **kwargs: Any) -> AsyncIterator[NativeEvent]:
        deps = self.deps
        timeout = get_settings().interview.run_timeout_seconds

        async def stream() -> AsyncIterator[NativeEvent]:
            try:
                async with (
                    asyncio.timeout(timeout),
                    self.agent.run_stream_events(self.text, **kwargs) as events,
                ):
                    async for event in events:
                        yield event  # ruff: ignore[yield-in-context-manager-in-async-generator] as in the adapter's own stream
            finally:
                if deps is not None:
                    run_guard.release(deps.session_id)

        return stream()


@dataclass
class InterviewStream:
    """What the endpoint sends: encoded events and how to label them."""

    body: AsyncIterator[str]
    media_type: str
    headers: Mapping[str, str] | None


def start(
    request: RunRequest,
    accept: str | None,
    deps: InterviewDeps,
    history: Sequence[ModelMessage],
) -> InterviewStream:
    """Prepare one turn: the run is started when the client reads the stream.

    Args:
        request: The parsed request; only its last user text is used.
        accept: The ``Accept`` header of the request.
        deps: The turn's dependencies (membership, session id, sessions).
        history: The stored history of the session (``load_history``).

    Returns:
        The encoded SSE stream.

    Raises:
        SessionBusyError: Another run of this session is in progress.
    """
    adapter = InterviewAdapter(
        agent=interview_agent,
        run_input=request.run_input,
        accept=accept,
        text=request.text,
        deps=deps,
    )
    run_guard.acquire(deps.session_id)

    async def store(result: AgentRunResult[str]) -> None:
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
    )
