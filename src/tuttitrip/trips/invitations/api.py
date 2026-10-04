"""Invitation endpoints.

Management lives under a trip (co-host and above). Joining takes the token in
the body, never in the path or query, and needs a logged-in account.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from tuttitrip.profiles.services.profile_service import (
    ProfileClaimedError,
    ProfileNotFoundError,
)
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
    InvitationProfileMismatchError,
    TooManyInvitationsError,
)

router = APIRouter(tags=["invitations"])

NOT_FOUND = "Invitation not found"
NO_STORE = {"Cache-Control": "no-store"}
INVITATION_NOT_FOUND: dict[int | str, dict[str, str]] = {
    404: {"description": "Unknown, expired, revoked or used-up invitation."}
}
PROFILE_NOT_FOUND = "Profile not found"
PROFILE_CLAIMED = "Profile already has an account"
PROFILE_MISMATCH = "This invitation is for a different profile"
ACCEPT_ERRORS: dict[int | str, dict[str, str]] = {
    404: {
        "description": (
            "Unknown, expired, revoked or used-up invitation (`Invitation not "
            "found`), or `profile_id` is not on the invitation's trip "
            "(`Profile not found`)."
        )
    },
    409: {
        "description": (
            "The profile already has an account or another person took it a "
            "moment ago, or a named invitation is for a different profile. "
            "Nothing changed: no membership, no use taken."
        )
    },
}


@router.post(
    "/trips/{trip_id}/invitations",  # ruff: ignore[fast-api-unused-path-parameter]
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "`profile_id` is not a profile of this trip."},
        409: {
            "description": (
                "20 working invitations already, or `profile_id` has an account."
            )
        },
    },
    dependencies=[requires(Feature.TRIPS_INVITATIONS, Access.WRITE), no_store()],
)
async def create_invitation(
    data: InvitationCreate, membership: TripCoHost, session: SessionDep
) -> InvitationCreated:
    """Create an invitation link token (co-host or host).

    The response is the only time the token is visible. The frontend builds
    `https://<frontend>/join#t=<token>` (and a QR code from it).

    With `profile_id` the invitation is named: whoever joins with it takes
    over that profile (without an account) and the link works once.

    Args:
        data: Lifetime, use limit and an optional profile to hand over.
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
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc
    except ProfileClaimedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, PROFILE_CLAIMED) from exc


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
    """Show the trip behind an invitation token and who can be taken over.

    `claimable_profiles` lists the trip's people without an account (id, name,
    age group, nothing else) so the joining person can point at themselves.

    Args:
        body: The token from the link's `#t=` fragment.
        user: The authenticated caller.
        session: Database session.

    Returns:
        The trip's name and destination and the claimable profiles.
    """
    try:
        return await invitation_service.preview(session, user.sub, body)
    except InvitationNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, NOT_FOUND, headers=NO_STORE
        ) from exc


@router.post(
    "/invitations/accept",
    responses=ACCEPT_ERRORS,
    dependencies=[requires(Feature.TRIPS_INVITATIONS, Access.WRITE), no_store()],
)
async def accept_invitation(
    body: InvitationAccept, user: CurrentUser, session: SessionDep
) -> JoinResult:
    """Join the trip as a member and get a profile linked to your account.

    With `profile_id` you take over an existing profile without an account
    (membership and profile link in one transaction) instead of getting a new
    one; a named invitation does that by itself. Idempotent: if you are on the
    trip already you get your profile back (`already_member: true`) and the
    link's use limit is not touched.

    Args:
        body: The token, an optional profile name and an optional profile to
            take over.
        user: The authenticated caller.
        session: Database session.

    Returns:
        The trip, your profile and your role.
    """
    try:
        return await invitation_service.accept(session, user.sub, body)
    except InvitationNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, NOT_FOUND, headers=NO_STORE
        ) from exc
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND, headers=NO_STORE
        ) from exc
    except ProfileClaimedError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, PROFILE_CLAIMED, headers=NO_STORE
        ) from exc
    except InvitationProfileMismatchError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, PROFILE_MISMATCH, headers=NO_STORE
        ) from exc
