"""Invitation endpoints.

Management lives under a trip (co-host and above). Joining takes the token in
the body, never in the path or query, and needs a logged-in account.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from tuttitrip.shared.auth.api import CurrentUser
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import no_store, requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripCoHost
from tuttitrip.trips.invitations.schemas import (
    InvitationAccept,
    InvitationCreate,
    InvitationCreated,
    InvitationPreview,
    InvitationRead,
    InvitationToken,
    JoinResult,
)
from tuttitrip.trips.invitations.services import invitation_service
from tuttitrip.trips.invitations.services.invitation_service import (
    InvitationNotFoundError,
    TooManyInvitationsError,
)

router = APIRouter(tags=["invitations"])

NOT_FOUND = "Invitation not found"
INVITATION_NOT_FOUND: dict[int | str, dict[str, str]] = {
    404: {"description": "Unknown, expired, revoked or used-up invitation."}
}


@router.post(
    "/trips/{trip_id}/invitations",  # ruff: ignore[fast-api-unused-path-parameter]
    status_code=status.HTTP_201_CREATED,
    dependencies=[requires(Feature.TRIPS_INVITATIONS, Access.WRITE), no_store()],
)
async def create_invitation(
    data: InvitationCreate, membership: TripCoHost, session: SessionDep
) -> InvitationCreated:
    """Create an invitation link token (co-host or host).

    The response is the only time the token is visible. The frontend builds
    `https://<frontend>/join#t=<token>` (and a QR code from it).

    Args:
        data: Lifetime and use limit.
        membership: The caller's (co-host) membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The invitation and its token.
    """
    try:
        return await invitation_service.create_invitation(session, membership, data)
    except TooManyInvitationsError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Too many working invitations (20); revoke one first",
        ) from exc


@router.get(
    "/trips/{trip_id}/invitations",  # ruff: ignore[fast-api-unused-path-parameter]
    dependencies=[requires(Feature.TRIPS_INVITATIONS, Access.READ)],
)
async def list_invitations(
    membership: TripCoHost, session: SessionDep
) -> list[InvitationRead]:
    """List the trip's invitations without the secret, newest first.

    Args:
        membership: The caller's (co-host) membership of ``{trip_id}``.
        session: Database session.

    Returns:
        Invitations with their use counts and dates.
    """
    return await invitation_service.list_invitations(session, membership)


@router.delete(
    "/trips/{trip_id}/invitations/{invitation_id}",  # ruff: ignore[fast-api-unused-path-parameter]
    dependencies=[requires(Feature.TRIPS_INVITATIONS, Access.WRITE)],
)
async def revoke_invitation(
    invitation_id: UUID, membership: TripCoHost, session: SessionDep
) -> InvitationRead:
    """Revoke an invitation (idempotent); people who joined stay members.

    Args:
        invitation_id: Invitation id from creation or the list.
        membership: The caller's (co-host) membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The invitation with `revoked_at`.
    """
    try:
        return await invitation_service.revoke_invitation(
            session, membership, invitation_id
        )
    except InvitationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from exc


@router.post(
    "/invitations/preview",
    responses=INVITATION_NOT_FOUND,
    dependencies=[requires(Feature.TRIPS_INVITATIONS, Access.READ), no_store()],
)
async def preview_invitation(
    body: InvitationToken, user: CurrentUser, session: SessionDep
) -> InvitationPreview:
    """Show the trip behind an invitation token (name and destination).

    Args:
        body: The token from the link's `#t=` fragment.
        user: The authenticated caller.
        session: Database session.

    Returns:
        The trip's name and destination.
    """
    try:
        return await invitation_service.preview(session, user.sub, body)
    except InvitationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from exc


@router.post(
    "/invitations/accept",
    responses=INVITATION_NOT_FOUND,
    dependencies=[requires(Feature.TRIPS_INVITATIONS, Access.WRITE), no_store()],
)
async def accept_invitation(
    body: InvitationAccept, user: CurrentUser, session: SessionDep
) -> JoinResult:
    """Join the trip as a member and get a profile linked to your account.

    Idempotent: if you are on the trip already you get your profile back
    (`already_member: true`) and the link's use limit is not touched.

    Args:
        body: The token and an optional profile name.
        user: The authenticated caller.
        session: Database session.

    Returns:
        The trip, your profile and your role.
    """
    try:
        return await invitation_service.accept(session, user.sub, body)
    except InvitationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from exc
