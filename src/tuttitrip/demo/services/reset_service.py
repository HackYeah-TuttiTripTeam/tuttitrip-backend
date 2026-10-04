"""Reset the demo account for the configured demo (login, then the data reset).

Shared by the deploy command (``seed_command``) and the internal endpoint the
worker's daily schedule calls, so the reset logic exists once.
"""

import asyncio
import logging
from collections.abc import Callable

import httpx

from tuttitrip.demo.logic.dataset import DEMO_ACCOUNT_NAME
from tuttitrip.demo.services import auth0_login, demo_service
from tuttitrip.shared.admin_users.services.management_client import (
    ManagementClient,
    ManagementError,
)
from tuttitrip.shared.auth.services.token_verifier import TokenVerifier
from tuttitrip.shared.config.settings import Settings
from tuttitrip.shared.db.session import get_engine

log = logging.getLogger(__name__)


async def demo_sub(
    settings: Settings,
    new_client: Callable[[], httpx.AsyncClient] = auth0_login.build_client,
) -> str:
    """The demo account's ``sub``, from a verified token of its own login.

    Args:
        settings: Auth0 and demo settings.
        new_client: Opens the HTTP client for the Auth0 call.

    Returns:
        The Auth0 subject the credentials belong to.
    """
    async with new_client() as client:
        session = await auth0_login.login(client, settings.auth0, settings.demo)
    verifier = TokenVerifier(
        domain=settings.auth0.domain,
        audience=settings.auth0.audience,
        roles_claim=settings.auth0.roles_claim,
    )
    return await asyncio.to_thread(lambda: verifier.verify(session.access_token).sub)


async def reset_demo(
    settings: Settings,
    new_client: Callable[[], httpx.AsyncClient] = auth0_login.build_client,
) -> int | None:
    """Reset the demo account's data (atomic, idempotent) and its Auth0 name.

    Args:
        settings: Auth0 and demo settings.
        new_client: Opens the HTTP client for the Auth0 call.

    Returns:
        The number of trips created, or None when the demo is switched off
        (empty ``TUTTITRIP_DEMO__TOKEN_SHA256``).

    Raises:
        DemoLoginError: Auth0 refused or could not be reached.
    """
    if not settings.demo.token_sha256:
        return None
    sub = await demo_sub(settings, new_client)
    trips = await demo_service.run_reset(get_engine(), sub)
    async with new_client() as http:
        await restore_account_name(ManagementClient(settings.auth0, http), sub)
    return trips


async def restore_account_name(management: ManagementClient, sub: str) -> None:
    """Put back the demo account's name; a failure never breaks the reset.

    Skipped without Management API credentials. Only the reason code is logged.

    Args:
        management: Management API client.
        sub: The demo account's Auth0 subject.
    """
    if not management.configured:
        return
    try:
        await management.update_user(sub, {"name": DEMO_ACCOUNT_NAME})
    except ManagementError as exc:
        log.warning("Demo account name not restored: reason=%s", exc.reason)
