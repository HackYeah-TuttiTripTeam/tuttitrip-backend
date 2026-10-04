"""Interview endpoints: the session of a trip and its "What we already know" view.

``POST .../agui`` is the AG-UI endpoint of the text interview; the session id is
its ``threadId``.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from tuttitrip.interview import constants
from tuttitrip.interview.schemas import (
    DraftPlanRead,
    InterviewSessionRead,
    InterviewState,
    KnowledgeRead,
    MessagesQuery,
    SessionRead,
    VoiceAnswer,
    VoiceCardRead,
    VoiceOffer,
)
from tuttitrip.interview.services import (
    agui_service,
    draft_plan_service,
    run_guard,
    session_service,
    voice_service,
)
from tuttitrip.interview.services.session_service import (
    HistoryIncompatibleError,
    NoProfileError,
    SessionNotFoundError,
)
from tuttitrip.planning.plans.schemas import PlanMissingInputs
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TRIP_NOT_FOUND, TripCoHost, TripMember
from tuttitrip.trips.services.trip_service import TripNotFoundError


class EventStreamResponse(StreamingResponse):
    """A streamed response documented as ``text/event-stream`` in OpenAPI."""

    media_type = "text/event-stream"


router = APIRouter(prefix="/trips/{trip_id}/interview", tags=["interview"])


def busy_detail(error: run_guard.SessionBusyError) -> str:
    """The 409 text for a busy session: it says whether a call or a turn holds it.

    Args:
        error: What the guard raised.

    Returns:
        The detail of the 409.
    """
    return BUSY_VOICE if error.kind == constants.RUN_VOICE else BUSY


NO_SESSION = "The trip has no open interview session"
NO_PROFILE = "You have no profile on this trip"
BUSY = "Another turn of this interview is still running"
BUSY_VOICE = "A voice call of this interview is still running"
VOICE_BUDGET = "The voice time of this trip's interview is used up"
VOICE_UNAVAILABLE = "The voice assistant is not available"
NO_CALL = "No such call on this trip"


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_200_OK: {"model": SessionRead, "description": "Resumed."}},
    dependencies=[requires(Feature.INTERVIEW, Access.WRITE)],
)
async def start_session(
    membership: TripMember, session: SessionDep, response: Response
) -> SessionRead:
    """Start the trip's interview, or resume the open one.

    The role picks whose interview it is. A co-host or host gets the trip's
    interview. A member gets their own, about their interests, with a session
    that only they can read; the assistant has tools for their own profile only.
    The session id is the AG-UI `threadId`. A second call returns the same
    session with 200; the first creates it with 201. 404 for a member without a
    profile on the trip.

    Args:
        membership: The caller's membership (any role).
        session: Database session.
        response: To set 200 when the session already existed.

    Returns:
        The open session.
    """
    try:
        read, created = await session_service.open_session(session, membership)
    except NoProfileError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_PROFILE) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return read


@router.get(
    "/sessions/current", dependencies=[requires(Feature.INTERVIEW, Access.READ)]
)
async def get_current_session(
    membership: TripMember,
    session: SessionDep,
    query: Annotated[MessagesQuery, Query()],
) -> InterviewSessionRead:
    """Read the open session with a page of the conversation to display.

    Only the questions and answers are listed; tool calls stay in the history.
    Use `dir=desc` to get the newest messages first (chat UI). 409 when the
    stored history cannot be read any more. A member reads only their own session.

    Args:
        membership: The caller's membership (any role).
        session: Database session.
        query: Page, size, direction and speaker filter of the messages.

    Returns:
        The session and its messages.
    """
    try:
        return await session_service.get_current(session, membership, query)
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SESSION) from exc
    except NoProfileError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_PROFILE) from exc
    except HistoryIncompatibleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/knowledge", dependencies=[requires(Feature.INTERVIEW, Access.READ)])
async def get_knowledge(membership: TripMember, session: SessionDep) -> KnowledgeRead:
    """Read the "What we already know" panel: trip, people, budget, preferences.

    Read from the trips and profiles services, so a value the host fixed
    through their endpoints shows here at once. `sources` says whether the
    assistant or the host set each value; `missing` is what is left to ask.
    A member sees only themselves: no budget and nobody else's data.

    Args:
        membership: The caller's membership (any role).
        session: Database session.

    Returns:
        The knowledge view.
    """
    try:
        return await session_service.get_knowledge(session, membership)
    except TripNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, TRIP_NOT_FOUND) from exc
    except NoProfileError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_PROFILE) from exc


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
    request: Request, membership: TripMember, session: SessionDep
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
    turn per session at a time. The role picks the tools: a co-host or host
    interviews about the trip; a member talks about their own interests, with
    tools that write only to their own profile and a session of their own.

    Args:
        request: The AG-UI request.
        membership: The caller's membership (any role).
        session: Database session (history is read before the stream starts).

    Returns:
        The SSE stream.
    """
    try:
        parsed = agui_service.read_request(await request.body())
        stream = await agui_service.begin(
            parsed, request.headers.get("accept"), membership, session
        )
    except agui_service.PromptError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SESSION) from exc
    except NoProfileError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_PROFILE) from exc
    except HistoryIncompatibleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except run_guard.SessionBusyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, busy_detail(exc)) from exc
    return EventStreamResponse(
        stream.body,
        media_type=stream.media_type,
        headers=dict(stream.headers or {}),
        background=BackgroundTask(run_guard.release, stream.claim),
    )


@router.post(
    "/voice/offer",
    responses={
        status.HTTP_409_CONFLICT: {"description": "A call or a text turn is running."},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Voice time used up."},
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
        return await voice_service.answer_offer(membership, body.sdp, body.locale)
    except run_guard.SessionBusyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, busy_detail(exc)) from exc
    except run_guard.VoiceBudgetError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, VOICE_BUDGET) from exc
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SESSION) from exc
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


@router.get(
    "/voice/{call_id}/card",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No such live call on this trip."}
    },
    dependencies=[requires(Feature.INTERVIEW, Access.READ)],
)
async def voice_card(call_id: str, membership: TripCoHost) -> VoiceCardRead:
    """The card of a live call, for the client to show next to the captions.

    A voice call has no AG-UI stream, so the client polls this while the call
    runs (the panel is polled the same way). The kind and options are the ones
    the server fixed, not the model's.

    Args:
        call_id: The id from the offer's answer.
        membership: The caller's membership (co-host or above).

    Returns:
        The card the assistant last showed, or null.
    """
    try:
        return VoiceCardRead(card=voice_service.shown_card(membership, call_id))
    except voice_service.CallNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_CALL) from exc


@router.post(
    "/voice/release",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "The trip has no interview."}
    },
    dependencies=[requires(Feature.INTERVIEW, Access.WRITE)],
)
async def voice_release(membership: TripCoHost) -> None:
    """End the voice call of this trip's interview, wherever it runs.

    For a call left on another device or by a tab that never hung up. The
    transcript is stored and the session is free when this returns. Does
    nothing when no call runs; a text turn is left to finish.

    Args:
        membership: The caller's membership (co-host or above).
    """
    try:
        await voice_service.release(membership)
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SESSION) from exc


@router.post(
    "/draft-plan",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": PlanMissingInputs,
            "description": (
                "No city yet (`detail.code` is `plan.missing_inputs`, `message` "
                "`Podaj miasto`), or an unplannable trip."
            ),
        },
    },
    dependencies=[requires(Feature.INTERVIEW, Access.WRITE)],
)
async def build_draft_plan(
    membership: TripCoHost, session: SessionDep
) -> DraftPlanRead:
    """Build a preliminary plan now ("Zbuduj plan teraz") at any point.

    Needs only the city. What the trip lacks is assumed in memory (two adults,
    one day, no budget limit, default preferences) and listed in `assumptions`;
    nothing is stored on the trip. The plan is a new version marked `draft` in
    its `params`; read it with `GET /trips/{id}/plans/{plan_id}`. The same data
    gives the same `plan_hash`. The agent's `build_plan_now` tool calls the same
    code.

    Args:
        membership: The caller's membership (co-host or above).
        session: Database session.

    Returns:
        The plan version and the assumptions made.
    """
    try:
        return await draft_plan_service.build(session, membership)
    except draft_plan_service.MissingCityError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            draft_plan_service.MISSING_CITY.model_dump(mode="json"),
        ) from exc
    except draft_plan_service.MissingInputsError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, exc.detail.model_dump(mode="json")
        ) from exc
    except draft_plan_service.PlanInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
