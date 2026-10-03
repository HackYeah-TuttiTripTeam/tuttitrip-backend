"""Invitations: issue a link, preview it, join with it, revoke it.

The token rules are those of access tokens (``token_service``): 32 random
bytes, only the SHA-256 stored, lookup by hash is the comparison, never logged
and never in an exception. An unknown, expired, revoked or used-up token all
raise the same ``InvitationNotFoundError``.

Joining happens in one transaction: a conditional ``UPDATE`` takes a use, then
the member row is added and the account is linked to a profile: either an
existing one without an account that the person takes over (chosen by them or
fixed by a named invitation; again one conditional ``UPDATE``, so of two racing
claims one wins and the other gets ``ProfileClaimedError``) or a new one.
Profiles of removed members have no account and can be taken over too.
"""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles.schemas import ClaimableProfile
from tuttitrip.profiles.services import profile_service
from tuttitrip.profiles.services.profile_service import (
    ProfileClaimedError,
    ProfileNotFoundError,
)
from tuttitrip.shared.permissions.services.token_service import (
    MAX_TOKEN_LENGTH,
    TOKEN_BYTES,
    hash_token,
)
from tuttitrip.trips import db as trips_db
from tuttitrip.trips.invitations import db
from tuttitrip.trips.invitations.models import TripInvitation
from tuttitrip.trips.invitations.schemas import (
    MAX_ACTIVE_PER_TRIP,
    PLACEHOLDER_NAME,
    InvitationAccept,
    InvitationCreate,
    InvitationCreated,
    InvitationPreview,
    InvitationRead,
    InvitationToken,
    JoinResult,
)
from tuttitrip.trips.schemas import TripMembership, TripRole


class InvitationNotFoundError(Exception):
    """Unknown, expired, revoked or used-up invitation (the cause is not told)."""


class InvitationProfileMismatchError(Exception):
    """The named invitation hands over a different profile than the one asked for."""


class TooManyInvitationsError(Exception):
    """The trip already has the maximum of working invitations."""


async def create_invitation(
    session: AsyncSession, membership: TripMembership, data: InvitationCreate
) -> InvitationCreated:
    """Issue an invitation for the trip and commit.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        data: Lifetime and use limit.

    Returns:
        The stored data plus the plain token (the only time it is available).

    Raises:
        TooManyInvitationsError: The trip has 20 working invitations already.
        ProfileNotFoundError: The named profile is not on this trip.
        ProfileClaimedError: The named profile already has an account.
    """
    if data.profile_id is not None:
        await profile_service.require_claimable(
            session, membership.trip_id, data.profile_id
        )
    now = datetime.now(UTC)
    if await db.count_usable(session, membership.trip_id, now) >= MAX_ACTIVE_PER_TRIP:
        raise TooManyInvitationsError
    token = secrets.token_urlsafe(TOKEN_BYTES)
    row = await db.insert_invitation(
        session,
        TripInvitation(
            trip_id=membership.trip_id,
            token_hash=hash_token(token),
            created_by_sub=membership.sub,
            expires_at=now + timedelta(days=data.expires_in_days),
            max_uses=data.max_uses,
            profile_id=data.profile_id,
        ),
    )
    await session.commit()
    fields = {name: getattr(row, name) for name in InvitationRead.model_fields}
    return InvitationCreated(**fields, token=token)


async def list_invitations(
    session: AsyncSession, membership: TripMembership
) -> list[InvitationRead]:
    """List the trip's invitations (never the secret).

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.

    Returns:
        Invitations, newest first.
    """
    rows = await db.select_for_trip(session, membership.trip_id)
    return [InvitationRead.model_validate(row) for row in rows]


async def revoke_invitation(
    session: AsyncSession, membership: TripMembership, invitation_id: UUID
) -> InvitationRead:
    """Revoke an invitation (idempotent) and commit; people who joined stay.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        invitation_id: Invitation id.

    Returns:
        The invitation's data.

    Raises:
        InvitationNotFoundError: No such invitation on this trip.
    """
    row = await db.select_one(session, membership.trip_id, invitation_id)
    if row is None:
        raise InvitationNotFoundError
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.commit()
    return InvitationRead.model_validate(row)


async def _find(
    session: AsyncSession, body: InvitationToken, sub: str
) -> tuple[db.Found, TripRole | None]:
    """The working invitation behind a token, and the caller's role on its trip.

    A caller who is on the trip already may use a used-up invitation (they
    consume nothing); anyone else needs a free use.

    Returns:
        The invitation with its trip, and the caller's role (None if not a member).

    Raises:
        InvitationNotFoundError: The token does not give the caller a way in.
    """
    if len(body.token) > MAX_TOKEN_LENGTH:
        raise InvitationNotFoundError
    found = await db.select_by_hash(session, hash_token(body.token), datetime.now(UTC))
    if found is None:
        raise InvitationNotFoundError
    row = found.invitation
    role = await trips_db.select_member_role(session, row.trip_id, sub)
    if role is None and row.uses >= row.max_uses:
        raise InvitationNotFoundError
    return found, role


async def preview(
    session: AsyncSession, sub: str, body: InvitationToken
) -> InvitationPreview:
    """Show the trip behind a token.

    Args:
        session: Open session.
        sub: Auth0 subject of the caller.
        body: The presented token.

    Returns:
        Trip name, destination, whether the caller is on it already and the
        profiles without an account they may take over (id, name, age group).

    Raises:
        InvitationNotFoundError: The token does not work for the caller.
    """
    found, role = await _find(session, body, sub)
    trip_id = found.invitation.trip_id
    named = found.invitation.profile_id
    claimable: list[ClaimableProfile] = []
    if role is None:
        claimable = [
            p
            for p in await profile_service.list_claimable(session, trip_id)
            if named is None or p.profile_id == named
        ]
    return InvitationPreview(
        trip_name=found.trip_name,
        destination=found.destination,
        already_member=role is not None,
        claimable_profiles=claimable,
    )


async def _join(
    session: AsyncSession,
    sub: str,
    row: TripInvitation,
    role: TripRole | None,
    body: InvitationAccept,
) -> JoinResult:
    """Make the caller a member with a profile; only what is missing is created.

    A use is taken only for a new member. An account that already has a profile
    on the trip (a member whose profile is missing is repaired the same way)
    keeps it instead of getting a second one (the chosen profile is ignored).
    Without a profile, the named or chosen profile is taken over, else a new one
    is created. Which profile that is gets decided first, so a bad request fails
    before any write.

    Returns:
        The join result.

    Raises:
        InvitationNotFoundError: No free use was left.
        ProfileNotFoundError: The profile in ``body.profile_id`` is not on this trip.
        ProfileClaimedError: The profile has an account or was taken meanwhile.
        InvitationProfileMismatchError: Named invitation, another profile asked.
    """
    profile_id = await profile_service.find_account_profile(session, row.trip_id, sub)
    claim_id = None
    if profile_id is None:
        if row.profile_id and body.profile_id and row.profile_id != body.profile_id:
            raise InvitationProfileMismatchError
        claim_id = row.profile_id or body.profile_id
    was_member = role is not None
    if role is None:
        if not await db.consume_use(session, row.id, datetime.now(UTC)):
            raise InvitationNotFoundError
        await trips_db.insert_member(session, row.trip_id, sub, TripRole.MEMBER)
        role = TripRole.MEMBER
    claimed = claim_id is not None
    if claim_id is not None:
        await profile_service.claim_profile(session, row.trip_id, claim_id, sub)
        profile_id = claim_id
    elif profile_id is None:
        profile_id = await profile_service.create_account_profile(
            session, row.trip_id, sub, body.display_name or PLACEHOLDER_NAME
        )
    await session.commit()
    return JoinResult(
        trip_id=row.trip_id,
        profile_id=profile_id,
        role=role,
        already_member=was_member,
        profile_claimed=claimed,
    )


async def _settled(session: AsyncSession, trip_id: UUID, sub: str) -> JoinResult:
    """The result after a concurrent request for the same account won the race.

    Returns:
        The join result of the account that is now on the trip with a profile.

    Raises:
        InvitationNotFoundError: The conflict was not that race (nothing joined).
    """
    role = await trips_db.select_member_role(session, trip_id, sub)
    profile_id = await profile_service.find_account_profile(session, trip_id, sub)
    if role is None or profile_id is None:
        raise InvitationNotFoundError
    return JoinResult(
        trip_id=trip_id, profile_id=profile_id, role=role, already_member=True
    )


async def accept(session: AsyncSession, sub: str, body: InvitationAccept) -> JoinResult:
    """Join the trip behind a token as a member, with a profile, and commit.

    Idempotent: someone already on the trip gets their profile back and no use
    is taken. A person removed earlier keeps their old profile (without an
    account) and gets a new one. A uniqueness conflict means the same account
    joined concurrently: the transaction rolls back (the use with it) and the
    caller gets what the other request created, or 404 if nothing joined.

    Args:
        session: Open session.
        sub: Auth0 subject of the caller.
        body: The token and an optional name for the profile.

    With ``profile_id`` (or a named invitation) the account takes over that
    profile instead of creating one. Any failure rolls the whole join back,
    the use included.

    Returns:
        The trip, the caller's profile and role.

    Raises:
        InvitationNotFoundError: The token does not work for the caller.
        InvitationProfileMismatchError: Named invitation, another profile asked.
        ProfileNotFoundError: The profile is not on the invitation's trip.
        ProfileClaimedError: The profile has an account or another claim won.
    """
    found, role = await _find(session, body, sub)
    row = found.invitation
    try:
        return await _join(session, sub, row, role, body)
    except IntegrityError:
        await session.rollback()
        return await _settled(session, row.trip_id, sub)
    except ProfileNotFoundError, ProfileClaimedError, InvitationProfileMismatchError:
        await session.rollback()
        raise
