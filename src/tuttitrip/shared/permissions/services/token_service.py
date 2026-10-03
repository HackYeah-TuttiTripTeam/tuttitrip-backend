"""Access tokens: create, check and revoke (access without an account).

The token is 32 random bytes (``secrets.token_urlsafe``). Only its SHA-256 is
stored and compared (in constant time); the token is never logged, never put
in an exception message and never stored on a request-scoped object.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.shared.permissions import db
from tuttitrip.shared.permissions.models import AccessToken
from tuttitrip.shared.permissions.schemas import (
    AccessTokenCreated,
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


async def create_token(session: AsyncSession, new: NewToken) -> AccessTokenCreated:
    """Create a token for a profile of a trip.

    Args:
        session: Open session.
        new: What to issue (profile membership already checked).

    Returns:
        The stored data plus the plain token (the only time it is available).
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    row = AccessToken(
        token_hash=hash_token(token),
        scope=new.scope,
        trip_id=new.trip_id,
        profile_id=new.profile_id,
        expires_at=datetime.now(UTC) + new.ttl,
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
    """Check a presented token.

    Args:
        session: Open session.
        token: The plain token from the request.
        now: Current time (tests).

    Returns:
        Proof bound to the token's trip and profile.

    Raises:
        InvalidTokenError: Unknown, expired or revoked.
    """
    now = now or datetime.now(UTC)
    digest = hash_token(token)
    row = await db.select_access_token_by_hash(session, digest)
    if (
        row is None
        or not hmac.compare_digest(row.token_hash, digest)
        or not _is_active(row, now)
    ):
        raise InvalidTokenError
    if row.last_used_at is None or now - row.last_used_at > LAST_USED_RESOLUTION:
        row.last_used_at = now
        await session.commit()
    return TokenAccess(
        token_id=row.id,
        trip_id=row.trip_id,
        profile_id=row.profile_id,
        scope=row.scope,
    )


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
