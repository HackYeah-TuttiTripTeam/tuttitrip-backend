"""Access tokens: create, check and revoke (access without an account).

The token is 32 random bytes (``secrets.token_urlsafe``). Only its SHA-256 is
stored, and looking the row up by that hash *is* the comparison: a timing
attack is pointless against a 256-bit random token. The token is never logged,
never put in an exception message and never stored on a request-scoped object.

The scope column stores the ``TokenScope`` member *name* in a VARCHAR without a
CHECK constraint, so a new scope needs no migration.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions import db
from tuttitrip.shared.permissions.models import AccessToken
from tuttitrip.shared.permissions.schemas import (
    AccessTokenCreated,
    AccessTokenQuery,
    AccessTokenRead,
    TokenAccess,
    TokenScope,
)

TOKEN_BYTES = 32
MAX_TOKEN_LENGTH = 128
LAST_USED_RESOLUTION = timedelta(minutes=1)


class InvalidTokenError(Exception):
    """Unknown, expired, revoked or wrong-scope token (the cause is not told)."""


class TokenNotFoundError(Exception):
    """No such token on this profile."""


def hash_token(token: str) -> str:
    """SHA-256 hex of a token.

    Args:
        token: The plain token.

    Returns:
        64 hex characters.
    """
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class NewToken:
    """What to issue: the caller has checked the profile is on the trip."""

    scope: TokenScope
    trip_id: UUID
    profile_id: UUID
    created_by: str
    ttl: timedelta
    replace_existing: bool = False
    """Revoke the profile's working tokens of this scope in the same transaction."""


async def create_token(session: AsyncSession, new: NewToken) -> AccessTokenCreated:
    """Create a token for a profile of a trip.

    Args:
        session: Open session.
        new: What to issue (profile membership already checked).

    Returns:
        The stored data plus the plain token (the only time it is available).

    Raises:
        TooManyTokensError: The profile already has 5 active tokens.
    """
    now = datetime.now(UTC)
    if new.replace_existing:
        await db.revoke_active_tokens(session, new.profile_id, new.scope, now)
    if await db.count_active_access_tokens(session, new.profile_id, now) >= (
        MAX_ACTIVE_PER_PROFILE
    ):
        raise TooManyTokensError
    token = secrets.token_urlsafe(TOKEN_BYTES)
    row = AccessToken(
        token_hash=hash_token(token),
        scope=new.scope,
        trip_id=new.trip_id,
        profile_id=new.profile_id,
        expires_at=now + new.ttl,
        created_by=new.created_by,
    )
    await db.insert_access_token(session, row)
    await session.commit()
    return AccessTokenCreated(
        **AccessTokenRead.model_validate(row).model_dump(), token=token
    )


def _is_active(row: AccessToken, now: datetime) -> bool:
    return row.revoked_at is None and row.expires_at > now


async def authenticate(
    session: AsyncSession, token: str, now: datetime | None = None
) -> TokenAccess:
    """Check a presented token (read-only; see ``touch`` for the last use).

    Args:
        session: Open session.
        token: The plain token from the request.
        now: Current time (tests).

    Returns:
        Proof bound to the token's trip and profile.

    Raises:
        InvalidTokenError: Unknown, too long, expired or revoked.
    """
    if len(token) > MAX_TOKEN_LENGTH:
        raise InvalidTokenError
    row = await db.select_access_token_by_hash(session, hash_token(token))
    if row is None or not _is_active(row, now or datetime.now(UTC)):
        raise InvalidTokenError
    return TokenAccess(
        token_id=row.id,
        trip_id=row.trip_id,
        profile_id=row.profile_id,
        scope=row.scope,
    )


async def touch(
    session: AsyncSession, token_id: UUID, now: datetime | None = None
) -> None:
    """Record the use of a token, at most once a minute.

    Called only after the scope matched, so a wrong-scope token leaves no trace.

    Args:
        session: Open session.
        token_id: Token id.
        now: Current time (tests).
    """
    now = now or datetime.now(UTC)
    if await db.update_last_used(session, token_id, now, now - LAST_USED_RESOLUTION):
        await session.commit()


MAX_ACTIVE_PER_PROFILE = 5


class TooManyTokensError(Exception):
    """The profile already has the maximum of active tokens."""


async def list_tokens(
    session: AsyncSession, trip_id: UUID, profile_id: UUID
) -> list[AccessTokenRead]:
    """Tokens of a profile, newest first (never the secret).

    Args:
        session: Open session.
        trip_id: Trip the caller was checked for.
        profile_id: Profile.

    Returns:
        Token data.
    """
    rows = await db.select_access_tokens(session, trip_id, profile_id)
    return [AccessTokenRead.model_validate(row) for row in rows]


async def revoke_token(
    session: AsyncSession, token_id: UUID, trip_id: UUID, profile_id: UUID
) -> AccessTokenRead:
    """Revoke a token of a profile (idempotent).

    Args:
        session: Open session.
        token_id: Token id.
        trip_id: Trip the caller was checked for.
        profile_id: Profile the token is bound to.

    Returns:
        The token's data.

    Raises:
        TokenNotFoundError: No such token on this profile.
    """
    row = await db.select_access_token(session, token_id, trip_id, profile_id)
    if row is None:
        raise TokenNotFoundError
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.commit()
    return AccessTokenRead.model_validate(row)


async def list_trip_tokens(
    session: AsyncSession,
    trip_id: UUID,
    scope: TokenScope,
    query: AccessTokenQuery,
) -> Page[AccessTokenRead]:
    """One page of a trip's tokens of one scope (never the secret).

    Args:
        session: Open session.
        trip_id: Trip the caller was checked for.
        scope: Token scope.
        query: Page, sort, direction and filters.

    Returns:
        The page.
    """
    page = await db.select_trip_tokens_page(
        session, trip_id, scope, query, datetime.now(UTC)
    )
    return Page[AccessTokenRead](
        items=[AccessTokenRead.model_validate(row) for row in page.items],
        total=page.total,
        page=page.page,
        size=page.size,
        pages=page.pages,
    )


async def revoke_trip_token(
    session: AsyncSession, token_id: UUID, trip_id: UUID, scope: TokenScope
) -> AccessTokenRead:
    """Revoke a token of one scope on a trip (idempotent).

    Args:
        session: Open session.
        token_id: Token id.
        trip_id: Trip the caller was checked for.
        scope: Token scope.

    Returns:
        The token's data.

    Raises:
        TokenNotFoundError: No such token on this trip and scope.
    """
    row = await db.select_trip_token(session, token_id, trip_id, scope)
    if row is None:
        raise TokenNotFoundError
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.commit()
    return AccessTokenRead.model_validate(row)
