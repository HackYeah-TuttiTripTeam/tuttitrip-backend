"""Interview endpoints: the session of a trip and its "What we already know" view.

``POST .../agui`` is the AG-UI endpoint of the text interview; the session id is
its ``threadId``.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from tuttitrip.interview.schemas import (
    InterviewSessionRead,
    InterviewState,
    KnowledgeRead,
    MessagesQuery,
    SessionRead,
    VoiceAnswer,
    VoiceOffer,
)
from tuttitrip.interview.services import (
    agui_service,
    run_guard,
    session_service,
    voice_service,
)
from tuttitrip.interview.services.session_service import (
    HistoryIncompatibleError,
    SessionNotFoundError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TRIP_NOT_FOUND, TripCoHost
from tuttitrip.trips.services.trip_service import TripNotFoundError


class EventStreamResponse(StreamingResponse):
    """A streamed response documented as ``text/event-stream`` in OpenAPI."""

    media_type = "text/event-stream"


router = APIRouter(prefix="/trips/{trip_id}/interview", tags=["interview"])

NO_SESSION = "The trip has no open interview session"
BUSY = "Another turn of this interview is still running"
VOICE_BUSY = "This interview already has a live call"
VOICE_UNAVAILABLE = "The voice assistant is not available"
NO_CALL = "No such call on this trip"


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_200_OK: {"model": SessionRead, "description": "Resumed."}},
    dependencies=[requires(Feature.INTERVIEW, Access.WRITE)],
)
async def start_session(
    membership: TripCoHost, session: SessionDep, response: Response
) -> SessionRead:
    """Start the trip's interview, or resume the open one.

    The session id is the AG-UI `threadId`. A second call returns the same
    session with 200; the first creates it with 201.

    Args:
        membership: The caller's membership (co-host or above).
        session: Database session.
        response: To set 200 when the session already existed.

    Returns:
        The open session.
    """
    read, created = await session_service.open_session(session, membership)
    if not created:
        response.status_code = status.HTTP_200_OK
    return read


@router.get(
    "/sessions/current", dependencies=[requires(Feature.INTERVIEW, Access.READ)]
)
async def get_current_session(
    membership: TripCoHost,
    session: SessionDep,
    query: Annotated[MessagesQuery, Query()],
) -> InterviewSessionRead:
    """Read the open session with a page of the conversation to display.

    Only the questions and answers are listed; tool calls stay in the history.
    Use `dir=desc` to get the newest messages first (chat UI). 409 when the
    stored history cannot be read any more.

    Args:
        membership: The caller's membership (co-host or above).
        session: Database session.
        query: Page, size, direction and speaker filter of the messages.

    Returns:
        The session and its messages.
    """
    try:
        return await session_service.get_current(session, membership, query)
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SESSION) from exc
    except HistoryIncompatibleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/knowledge", dependencies=[requires(Feature.INTERVIEW, Access.READ)])
async def get_knowledge(membership: TripCoHost, session: SessionDep) -> KnowledgeRead:
    """Read the "What we already know" panel: trip, people, budget, preferences.

    Read from the trips and profiles services, so a value the host fixed
    through their endpoints shows here at once. `sources` says whether the
    assistant or the host set each value; `missing` is what is left to ask.

    Args:
        membership: The caller's membership (co-host or above).
        session: Database session.

    Returns:
        The knowledge view.
    """
    try:
        return await session_service.get_knowledge(session, membership)
    except TripNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, TRIP_NOT_FOUND) from exc


@router.post(
    "/agui",
    response_class=EventStreamResponse,
    responses={
        status.HTTP_200_OK: {
            "model": InterviewState,
            "description": (
                "AG-UI 1.0 events (SSE) of one turn. The schema is the "
                "`snapshot` of `STATE_SNAPSHOT`."
            ),
        },
        status.HTTP_404_NOT_FOUND: {"description": "No such session on this trip."},
        status.HTTP_409_CONFLICT: {
            "description": "A turn is running, or the history is unreadable."
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Not a RunAgentInput, bad threadId, or no user text."
        },
    },
    dependencies=[requires(Feature.INTERVIEW, Access.WRITE)],
)
async def run_turn(
    request: Request, membership: TripCoHost, session: SessionDep
) -> EventStreamResponse:
    """Run one turn of the text interview and stream it as AG-UI events (SSE).

    Body: AG-UI `RunAgentInput` with `threadId` = the session id from
    `POST .../sessions`. Only the text of the **last user message** is used:
    the server keeps the history, the tools and the state, and ignores the
    client's `state`, `tools`, `resume` and earlier messages. The answer to a
    card is that text too. Events: `RUN_STARTED`, `TEXT_MESSAGE_*`,
    `TOOL_CALL_*`, `STATE_SNAPSHOT` (`InterviewState`) after every tool that
    changes the panel or the card, and `RUN_FINISHED` or `RUN_ERROR` (Polish
    `message`, `code`: `spend_limit`, `timeout`, `unavailable`, `error`). One
    turn per session at a time.

    Args:
        request: The AG-UI request.
        membership: The caller's membership (co-host or above).
        session: Database session (history is read before the stream starts).

    Returns:
        The SSE stream.
    """
    try:
        parsed = agui_service.read_request(await request.body())
        history = await session_service.load_history(
            session, membership, parsed.thread_id
        )
        deps = agui_service.new_deps(membership, parsed.thread_id)
        stream = agui_service.start(
            parsed, request.headers.get("accept"), deps, history
        )
    except agui_service.PromptError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SESSION) from exc
    except HistoryIncompatibleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except run_guard.SessionBusyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, BUSY) from exc
    return EventStreamResponse(
        stream.body, media_type=stream.media_type, headers=dict(stream.headers or {})
    )


@router.post(
    "/voice/offer",
    responses={
        status.HTTP_409_CONFLICT: {"description": "This interview has a live call."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The voice service refused, or the assistant did not join."
        },
    },
    dependencies=[requires(Feature.INTERVIEW, Access.WRITE)],
)
async def voice_offer(body: VoiceOffer, membership: TripCoHost) -> VoiceAnswer:
    """Start a voice interview: relay the browser's WebRTC offer.

    The browser sends audio straight to OpenAI; the server attaches a sideband
    that runs the interview tools with the caller's membership. The answer is
    returned once the sideband is attached. The conversation is stored in the
    interview session when it ends (hang-up, time limit). The panel is not
    pushed during a call: re-read `GET .../knowledge`.

    Args:
        body: The SDP offer.
        membership: The caller's membership (co-host or above).

    Returns:
        The SDP answer and the call id.
    """
    try:
        return await voice_service.answer_offer(membership, body.sdp)
    except voice_service.VoiceBusyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, VOICE_BUSY) from exc
    except voice_service.VoiceUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, VOICE_UNAVAILABLE
        ) from exc


@router.post(
    "/voice/{call_id}/hangup",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No such call on this trip."}
    },
    dependencies=[requires(Feature.INTERVIEW, Access.WRITE)],
)
async def voice_hangup(call_id: str, membership: TripCoHost) -> None:
    """End a voice interview and store its transcript in the session.

    Args:
        call_id: The id from the offer's answer.
        membership: The caller's membership (co-host or above).
    """
    try:
        await voice_service.hang_up(membership, call_id)
    except voice_service.CallNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_CALL) from exc
