"""Block, unblock and delete accounts in our API and in Auth0.

The API checks the block itself on every request (an Auth0 block does not end
tokens already issued), so our side changes first and takes effect at once; the
Auth0 call follows. When Auth0 fails the local block stays and the call can be
repeated. Lifting a block goes the other way round (Auth0 first).
"""

from collections.abc import Awaitable, Iterable
from contextlib import suppress

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.shared.admin_users.services import erasure
from tuttitrip.shared.admin_users.services.management_client import (
    NOT_FOUND_REASON,
    ManagementClient,
    ManagementError,
)
from tuttitrip.shared.permissions.services import permission_service

DISCORD_SUB_PREFIXES = ("discord|", "oauth2|discord|")
"""Auth0 user id forms of a Discord login (social or generic OAuth2 connection)."""


class ProtectedAccountError(Exception):
    """The admin's own account or a superadmin cannot be blocked or deleted."""


class AccountNotFoundError(Exception):
    """Auth0 has no such account."""


def _guard(actor_sub: str, sub: str, protected_discord_ids: Iterable[str]) -> None:
    """Refuse the admin's own account and the superadmins' Discord logins."""
    ids = set(protected_discord_ids)
    discord = any(
        sub.startswith(prefix) and sub.removeprefix(prefix) in ids
        for prefix in DISCORD_SUB_PREFIXES
    )
    if sub == actor_sub or discord:
        msg = "Your own account and superadmin accounts cannot be blocked or deleted"
        raise ProtectedAccountError(msg)


async def _auth0(call: Awaitable[None], sub: str) -> None:
    """Await an Auth0 call; an unknown account becomes ``AccountNotFoundError``."""
    try:
        await call
    except ManagementError as exc:
        if exc.reason == NOT_FOUND_REASON:
            raise AccountNotFoundError(sub) from None
        raise


async def block(
    session: AsyncSession,
    client: ManagementClient,
    actor_sub: str,
    sub: str,
    protected_discord_ids: Iterable[str] = (),
) -> None:
    """Block the account in the API (committed), then in Auth0.

    Args:
        session: Open session.
        client: Management API client.
        actor_sub: The admin.
        sub: Auth0 subject of the account.
        protected_discord_ids: Discord ids of the superadmins (settings).

    Raises:
        ProtectedAccountError: Own account or a superadmin.
        AccountNotFoundError: Auth0 has no such account (our block is lifted).
        ManagementError: Auth0 failed; our block stays, repeat the call.
    """
    _guard(actor_sub, sub, protected_discord_ids)
    await permission_service.block_account(session, actor_sub, sub)
    try:
        await _auth0(client.set_blocked(sub, blocked=True), sub)
    except AccountNotFoundError:
        await permission_service.unblock_account(session, actor_sub, sub)
        raise


async def unblock(
    session: AsyncSession, client: ManagementClient, actor_sub: str, sub: str
) -> None:
    """Lift the block in Auth0, then in the API, audit it.

    Args:
        session: Open session.
        client: Management API client.
        actor_sub: The admin.
        sub: Auth0 subject of the account.

    Raises:
        AccountNotFoundError: Auth0 has no such account.
        ManagementError: Auth0 failed; nothing changed on our side.
    """
    await _auth0(client.set_blocked(sub, blocked=False), sub)
    await permission_service.unblock_account(session, actor_sub, sub)


async def delete(
    session: AsyncSession,
    client: ManagementClient,
    actor_sub: str,
    sub: str,
    protected_discord_ids: Iterable[str] = (),
) -> None:
    """Block, delete in Auth0, then clear the account's data.

    The block comes first so the account stops working at once. An account
    already gone from Auth0 is cleaned up all the same, so a half-finished
    deletion can be repeated. The audit entry carries the counts of what was
    handed over, deleted, removed and revoked.

    Args:
        session: Open session.
        client: Management API client.
        actor_sub: The admin.
        sub: Auth0 subject of the account.
        protected_discord_ids: Discord ids of the superadmins (settings).

    Raises:
        ProtectedAccountError: Own account or a superadmin.
        ManagementError: Auth0 failed; the account stays blocked, repeat the call.
    """
    _guard(actor_sub, sub, protected_discord_ids)
    await permission_service.block_account(session, actor_sub, sub)
    with suppress(AccountNotFoundError):  # already gone: finish the cleanup
        await _auth0(client.delete_user(sub), sub)
    counts = await erasure.erase(session, sub)
    counts |= await permission_service.revoke_issued_tokens(session, sub)
    await permission_service.block_account(
        session, actor_sub, sub, deleted=True, change=counts
    )
