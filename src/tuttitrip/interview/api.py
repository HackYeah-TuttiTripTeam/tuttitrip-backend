"""Interview endpoints: the session of a trip and its "What we already know" view.

The AG-UI endpoint (issue #56) uses the session id as ``threadId``.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from tuttitrip.interview.schemas import (
    InterviewSessionRead,
    KnowledgeRead,
    MessagesQuery,
    SessionRead,
)
from tuttitrip.interview.services import session_service
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
