"""Links for people without an account: tokens bound to one profile."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles import db
from tuttitrip.profiles.schemas import AccessTokenCreate
from tuttitrip.profiles.services.profile_service import ProfileNotFoundError
from tuttitrip.shared.permissions.schemas import (
    AccessTokenCreated,
    AccessTokenRead,
    TokenScope,
)
from tuttitrip.shared.permissions.services import token_service
from tuttitrip.shared.permissions.services.token_service import (
    NewToken,
    TokenNotFoundError,
)
from tuttitrip.trips.schemas import TripMembership


async def _require_profile(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> None:
    if await db.select_profile(session, membership.trip_id, profile_id) is None:
        raise ProfileNotFoundError(str(profile_id))


async def create_vote_token(
    session: AsyncSession,
    membership: TripMembership,
    profile_id: UUID,
    data: AccessTokenCreate,
) -> AccessTokenCreated:
    """Issue a voting token for a profile of the trip.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        profile_id: Profile the token is bound to.
        data: Lifetime.

    Returns:
        The token data and the plain token (shown once).
    """
    await _require_profile(session, membership, profile_id)
    return await token_service.create_token(
        session,
        NewToken(
            scope=TokenScope.VOTE,
            trip_id=membership.trip_id,
            profile_id=profile_id,
            created_by=membership.sub,
            ttl=timedelta(days=data.expires_in_days),
        ),
    )


async def revoke_token(
    session: AsyncSession,
    membership: TripMembership,
    profile_id: UUID,
    token_id: UUID,
) -> AccessTokenRead:
    """Revoke a token of a profile of the trip.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        profile_id: Profile the token is bound to.
        token_id: Token id.

    Returns:
        The token's data.

    Raises:
        ProfileNotFoundError: No such token or profile on this trip.
    """
    await _require_profile(session, membership, profile_id)
    try:
        return await token_service.revoke_token(
            session, token_id, membership.trip_id, profile_id
        )
    except TokenNotFoundError as exc:
        raise ProfileNotFoundError(str(token_id)) from exc
