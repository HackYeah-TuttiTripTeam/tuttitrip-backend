"""Plan proposals (nested under a trip)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from tuttitrip.planning.proposals.schemas import (
    OutdatedDetail,
    OutdatedError,
    ProposalCreate,
    ProposalRead,
    ResponseCreate,
)
from tuttitrip.planning.proposals.services import proposal_service
from tuttitrip.planning.proposals.services.proposal_service import (
    ProposalNotFoundError,
    ProposalOutdatedError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripHost, TripMember

router = APIRouter(prefix="/trips/{trip_id}/proposals", tags=["planning"])

NOT_FOUND = {404: {"description": "Trip, proposal or plan not found."}}
OUTDATED = {
    409: {
        "model": OutdatedError,
        "description": "The proposal is about an older plan version.",
    }
}


def _outdated(error: ProposalOutdatedError) -> JSONResponse:
    body = OutdatedError(
        detail=OutdatedDetail(message=str(error), latest_plan_id=error.latest_plan_id)
    )
    return JSONResponse(body.model_dump(mode="json"), status.HTTP_409_CONFLICT)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ProposalRead,
    summary="Send a plan version to the members",
    description=(
        "The host proposes the latest stored plan (or a given version that still "
        "is the latest). Every member with an account gets a notification and "
        "approves, rejects or comments. Sending again replaces the open "
        "proposal. A person without an account is listed apart: their opinion "
        "comes from a voting link."
    ),
    responses={**NOT_FOUND, **OUTDATED},
    dependencies=[requires(Feature.PLANNING_PROPOSALS, Access.WRITE)],
)
async def send_proposal(
    session: SessionDep, membership: TripHost, data: ProposalCreate | None = None
) -> ProposalRead | JSONResponse:
    """Send the plan to the members.

    Args:
        session: Database session.
        membership: The host's membership of ``{trip_id}``.
        data: Optional version to send.

    Returns:
        The proposal, or a 409 body when the version is not the latest.

    Raises:
        HTTPException: 404 when the trip has no plan.
    """
    try:
        return await proposal_service.send_proposal(
            session, membership, data or ProposalCreate()
        )
    except ProposalOutdatedError as exc:
        return _outdated(exc)
    except ProposalNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found") from exc


@router.get(
    "/current",
    summary="The proposal sent last, with its status",
    description=(
        "`status` is `approved` when every member with an account approved, "
        "`rejected` when somebody rejects, `outdated` when the plan changed "
        "after it was sent, else `pending`. The tally counts approvals, "
        "rejections and comments."
    ),
    responses={404: {"description": "Nothing was sent, or no such trip."}},
    dependencies=[requires(Feature.PLANNING_PROPOSALS, Access.READ)],
)
async def get_current_proposal(
    session: SessionDep, membership: TripMember
) -> ProposalRead:
    """The proposal sent last.

    Args:
        session: Database session.
        membership: The caller's membership of ``{trip_id}``.

    Returns:
        The proposal.

    Raises:
        HTTPException: 404 when nothing was sent.
    """
    try:
        return await proposal_service.current_proposal(session, membership)
    except ProposalNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No proposal yet") from exc


@router.put(
    "/{proposal_id}/response",
    response_model=ProposalRead,
    summary="Approve, reject or comment on a proposal",
    description=(
        "Any member with an account answers; a new answer replaces the earlier "
        "one. 409 `proposal.outdated` when the plan changed after it was sent. "
        "A comment needs a remark."
    ),
    responses={**NOT_FOUND, **OUTDATED},
    dependencies=[requires(Feature.PLANNING_PROPOSALS, Access.WRITE)],
)
async def respond_to_proposal(
    session: SessionDep, membership: TripMember, proposal_id: UUID, data: ResponseCreate
) -> ProposalRead | JSONResponse:
    """Answer a proposal.

    Args:
        session: Database session.
        membership: The caller's membership of ``{trip_id}``.
        proposal_id: The proposal.
        data: The decision and the remark.

    Returns:
        The proposal with the new counts, or a 409 body when it is outdated.

    Raises:
        HTTPException: 404 when the trip has no such proposal.
    """
    try:
        return await proposal_service.respond(session, membership, proposal_id, data)
    except ProposalOutdatedError as exc:
        return _outdated(exc)
    except ProposalNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposal not found") from exc
