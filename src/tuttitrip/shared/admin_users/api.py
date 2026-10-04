"""``GET /admin/users``: accounts from the Auth0 Management API."""

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from tuttitrip.shared.admin_users.schemas import AdminUserRead, UserQuery
from tuttitrip.shared.admin_users.services.management_client import (
    ManagementClient,
    ManagementError,
)
from tuttitrip.shared.config.settings import get_settings
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
    client: Annotated[ManagementClient, Depends(get_management_client)],
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
    if not client.configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Auth0 Management API is not configured",
        )
    try:
        return await client.list_users(query)
    except ManagementError as exc:
        log.warning("Auth0 user list failed: %s", exc.reason)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Auth0 request failed"
        ) from None
