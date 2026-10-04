"""``/admin/users``: accounts from the Auth0 Management API, block and delete."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from tuttitrip.shared.admin_users.schemas import AdminUserRead, UserQuery
from tuttitrip.shared.admin_users.services import account_service
from tuttitrip.shared.admin_users.services.account_service import (
    AccountNotFoundError,
    ProtectedAccountError,
)
from tuttitrip.shared.admin_users.services.management_client import (
    ManagementClient,
    ManagementError,
)
from tuttitrip.shared.auth.api import CurrentUser
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature

router = APIRouter(prefix="/admin/users", tags=["admin"])
log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_management_client() -> ManagementClient:
    """Build the process-wide client (it caches the M2M token).

    Returns:
        The cached client.
    """
    return ManagementClient(get_settings().auth0)


ClientDep = Annotated[ManagementClient, Depends(get_management_client)]
_AUTH0_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": "Auth0 has no such account."},
    409: {"description": "Own account or a superadmin."},
    502: {"description": "Auth0 did not answer correctly."},
    503: {"description": "The Management API credentials are not configured."},
}


def _protected_ids() -> list[str]:
    return get_settings().admin.protected_discord_ids


def _require_configured(client: ManagementClient) -> None:
    if not client.configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Auth0 Management API is not configured",
        )


@router.get(
    "",
    dependencies=[requires(Feature.ADMIN_USERS, Access.READ)],
    responses={
        502: {"description": "Auth0 did not answer correctly."},
        503: {"description": "The Management API credentials are not configured."},
    },
)
async def list_auth0_users(
    query: Annotated[UserQuery, Query()],
    client: ClientDep,
) -> Page[AdminUserRead]:
    """Search Auth0 accounts (e-mail, name, provider, last login, blocked).

    Auth0 search reaches only the first 1000 matches; narrow with `q`.

    Args:
        query: Page, sort and filters.
        client: Management API client.

    Returns:
        One page of accounts.

    Raises:
        HTTPException: 503 when unconfigured, 502 when Auth0 fails.
    """
    _require_configured(client)
    try:
        return await client.list_users(query)
    except ManagementError as exc:
        log.warning("Auth0 user list failed: %s", exc.reason)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Auth0 request failed"
        ) from None


@asynccontextmanager
async def _auth0_errors(client: ManagementClient) -> AsyncGenerator[None]:
    """Turn account-service failures into HTTP errors (503, 404, 409, 502).

    Args:
        client: Management API client (checked for configuration first).

    Yields:
        Nothing; the body runs the account operation.

    Raises:
        HTTPException: 503, 404, 409 or 502.
    """
    _require_configured(client)
    try:
        yield
    except AccountNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found") from None
    except ProtectedAccountError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except ManagementError as exc:
        log.warning("Auth0 account change failed: %s", exc.reason)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Auth0 request failed"
        ) from None


@router.post(
    "/{sub}/block",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[requires(Feature.ADMIN_USERS, Access.WRITE)],
    responses=_AUTH0_RESPONSES,
)
async def block_user(
    sub: str, admin: CurrentUser, session: SessionDep, client: ClientDep
) -> Response:
    """Block an account in Auth0 and in this API (tokens already issued stop too).

    Args:
        sub: Auth0 user id.
        admin: The calling administrator.
        session: Database session.
        client: Management API client.

    Returns:
        Empty 204.
    """
    async with _auth0_errors(client):
        await account_service.block(session, client, admin.sub, sub, _protected_ids())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{sub}/block",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[requires(Feature.ADMIN_USERS, Access.WRITE)],
    responses=_AUTH0_RESPONSES,
)
async def unblock_user(
    sub: str, admin: CurrentUser, session: SessionDep, client: ClientDep
) -> Response:
    """Lift a block in Auth0 and in this API.

    Args:
        sub: Auth0 user id.
        admin: The calling administrator.
        session: Database session.
        client: Management API client.

    Returns:
        Empty 204.
    """
    async with _auth0_errors(client):
        await account_service.unblock(session, client, admin.sub, sub)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{sub}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[requires(Feature.ADMIN_USERS, Access.WRITE)],
    responses=_AUTH0_RESPONSES,
)
async def delete_user(
    sub: str, admin: CurrentUser, session: SessionDep, client: ClientDep
) -> Response:
    """Delete an account in Auth0 and clear its data.

    Trips it hosts pass to the co-host who joined first; **a trip without a
    co-host is deleted** with everything under it. Memberships, roles, grants,
    issued access tokens and open invitations go; profiles are detached from
    the account; expenses stay. The account is blocked first and stays refused
    afterwards. The audit entry carries the counts.

    Args:
        sub: Auth0 user id.
        admin: The calling administrator.
        session: Database session.
        client: Management API client.

    Returns:
        Empty 204.
    """
    async with _auth0_errors(client):
        await account_service.delete(session, client, admin.sub, sub, _protected_ids())
    return Response(status_code=status.HTTP_204_NO_CONTENT)
