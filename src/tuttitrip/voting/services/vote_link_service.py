"""Voting links: tokens of scope `vote`, one working link per person.

The token rules are those of `shared.permissions.services.token_service`: 32
random bytes, only the SHA-256 stored, shown once, never logged. A new link for
a person revokes the previous one in the same transaction.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.profiles.services.profile_service import ProfileNotFoundError
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.schemas import (
    AccessTokenQuery,
    AccessTokenRead,
    AccessTokenState,
    TokenScope,
)
from tuttitrip.shared.permissions.services import token_service
from tuttitrip.shared.permissions.services.token_service import (
    NewToken,
    TokenNotFoundError,
)
from tuttitrip.trips.schemas import TripMembership
from tuttitrip.voting.schemas import (
    VOTE_PATH,
    VoteLinkCreate,
    VoteLinkCreated,
    VoteLinkRead,
)


class VoteLinkNotFoundError(Exception):
    """No such voting link on this trip."""


class VoteLinkProfileHasAccountError(Exception):
    """The person has an account and votes in the app, not through a link."""


def _state(token: AccessTokenRead, now: datetime) -> AccessTokenState:
    if token.revoked_at is not None:
        return AccessTokenState.REVOKED
    if token.expires_at <= now:
        return AccessTokenState.EXPIRED
    return AccessTokenState.ACTIVE


def _link(
    token: AccessTokenRead, names: dict[UUID, str], now: datetime
) -> VoteLinkRead:
    return VoteLinkRead(
        id=token.id,
        profile_id=token.profile_id,
        profile_name=names.get(token.profile_id),
        state=_state(token, now),
        created_at=token.created_at,
        expires_at=token.expires_at,
        revoked_at=token.revoked_at,
        last_used_at=token.last_used_at,
    )


async def _names(session: AsyncSession, membership: TripMembership) -> dict[UUID, str]:
    profiles = await profile_service.list_profiles(session, membership)
    return {p.id: p.display_name for p in profiles}


async def _profile(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> ProfileRead:
    for profile in await profile_service.list_profiles(session, membership):
        if profile.id == profile_id:
            return profile
    raise ProfileNotFoundError(str(profile_id))


async def create_link(
    session: AsyncSession, membership: TripMembership, data: VoteLinkCreate
) -> VoteLinkCreated:
    """Issue a voting link for a person without an account and commit.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        data: Person and lifetime.

    Returns:
        The link and its token (the only time it is available).

    Raises:
        ProfileNotFoundError: The profile is not on this trip.
        VoteLinkProfileHasAccountError: The profile belongs to an account.
    """
    profile = await _profile(session, membership, data.profile_id)
    if profile.user_sub is not None:
        raise VoteLinkProfileHasAccountError(str(profile.id))
    created = await token_service.create_token(
        session,
        NewToken(
            scope=TokenScope.VOTE,
            trip_id=membership.trip_id,
            profile_id=profile.id,
            created_by=membership.sub,
            ttl=timedelta(days=data.expires_in_days),
            replace_existing=True,
        ),
    )
    link = _link(created, {profile.id: profile.display_name}, datetime.now(UTC))
    return VoteLinkCreated(
        **link.model_dump(), token=created.token, url=f"{VOTE_PATH}#t={created.token}"
    )


async def list_links(
    session: AsyncSession, membership: TripMembership, query: AccessTokenQuery
) -> Page[VoteLinkRead]:
    """One page of the trip's voting links, without secrets.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        query: Page, sort and filters.

    Returns:
        The page.
    """
    page = await token_service.list_trip_tokens(
        session, membership.trip_id, TokenScope.VOTE, query
    )
    names = await _names(session, membership)
    now = datetime.now(UTC)
    return Page[VoteLinkRead](
        items=[_link(t, names, now) for t in page.items],
        total=page.total,
        page=page.page,
        size=page.size,
        pages=page.pages,
    )


async def revoke_link(
    session: AsyncSession, membership: TripMembership, link_id: UUID
) -> VoteLinkRead:
    """Revoke a voting link (idempotent); votes already cast stay.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        link_id: Link id from creation or the list.

    Returns:
        The link with `revoked_at`.

    Raises:
        VoteLinkNotFoundError: No such link on this trip.
    """
    try:
        token = await token_service.revoke_trip_token(
            session, link_id, membership.trip_id, TokenScope.VOTE
        )
    except TokenNotFoundError as exc:
        raise VoteLinkNotFoundError(str(link_id)) from exc
    return _link(token, await _names(session, membership), datetime.now(UTC))
