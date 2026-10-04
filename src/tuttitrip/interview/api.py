"""Interview endpoints: the session of a trip and its "What we already know" view.

``POST .../agui`` is the AG-UI endpoint of the text interview; the session id is
its ``threadId``.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from tuttitrip.interview.schemas import (
    InterviewSessionRead,
    KnowledgeRead,
    MessagesQuery,
    SessionRead,
)
from tuttitrip.interview.services import agui_service, run_guard, session_service
from tuttitrip.interview.services.session_service import (
    HistoryIncompatibleError,
    SessionNotFoundError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TRIP_NOT_FOUND, TripCoHost
from tuttitrip.trips.services.trip_service import TripNotFoundError

router = APIRouter(prefix="/trips/{trip_id}/interview", tags=["interview"])

NO_SESSION = "The trip has no open interview session"
BUSY = "Another turn of this interview is still running"


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
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {
            "content": {"text/event-stream": {}},
            "description": "AG-UI 1.0 events (SSE) of one turn.",
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
) -> StreamingResponse:
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
    return StreamingResponse(
        stream.body, media_type=stream.media_type, headers=dict(stream.headers or {})
    )
