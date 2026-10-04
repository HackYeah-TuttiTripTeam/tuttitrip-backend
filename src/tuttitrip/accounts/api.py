"""``PATCH /me/account``: the caller changes the name of their own account."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from tuttitrip.accounts.schemas import (
    AccountRead,
    AccountSource,
    AccountUpdate,
    ProviderManagedDetail,
    ProviderManagedError,
)
from tuttitrip.accounts.services import account_service
from tuttitrip.shared.admin_users.api import get_management_client
from tuttitrip.shared.admin_users.services.management_client import (
    ManagementClient,
    ManagementError,
)
from tuttitrip.shared.auth.api import CurrentUser
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature

router = APIRouter(prefix="/me/account", tags=["auth"])
log = logging.getLogger(__name__)

_PROVIDER_NAMES = {AccountSource.GOOGLE: "Google", AccountSource.DISCORD: "Discord"}


@router.patch(
    "",
    dependencies=[requires(Feature.ACCOUNTS_PROFILE, Access.WRITE)],
    responses={
        409: {
            "model": ProviderManagedError,
            "description": (
                "The account signs in with Google, Discord or another provider, "
                "which owns its data. Nothing changed. Clients map by "
                "`detail.code` and `detail.source`; `detail.message` is for "
                "developers."
            ),
        },
        502: {"description": "Auth0 did not answer correctly."},
        503: {"description": "The Management API credentials are not configured."},
    },
)
async def update_my_account(
    data: AccountUpdate,
    user: CurrentUser,
    client: Annotated[ManagementClient, Depends(get_management_client)],
) -> AccountRead:
    """Change the name of the caller's own account (e-mail and password only).

    The account is always the one of the access token; a `sub` in the body is
    ignored. The new name shows in tokens after the next login. E-mail and
    password are changed through Auth0 Universal Login, not here.

    Args:
        data: The new name.
        user: The authenticated caller.
        client: Management API client.

    Returns:
        The account after the change.

    Raises:
        HTTPException: 409 for a social account, 503 when unconfigured,
            502 when Auth0 fails.
    """
    try:
        return await account_service.update_account(client, user.sub, data)
    except account_service.ProviderManagedAccountError as exc:
        name = _PROVIDER_NAMES.get(exc.source, "your login provider")
        detail = ProviderManagedDetail(
            source=exc.source,
            message=f"Account data comes from {name}; change it there.",
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail.model_dump(mode="json")
        ) from exc
    except account_service.ManagementNotConfiguredError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Auth0 Management API is not configured",
        ) from exc
    except ManagementError as exc:
        log.warning("Auth0 account update failed: %s", exc.reason)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Auth0 request failed"
        ) from None
