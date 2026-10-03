"""Reset the demo account for the configured demo (login, then the data reset).

Shared by the deploy command (``seed_command``) and the internal endpoint the
worker's daily schedule calls, so the reset logic exists once.
"""

import asyncio
from collections.abc import Callable

import httpx

from tuttitrip.demo.services import auth0_login, demo_service
from tuttitrip.shared.auth.services.token_verifier import TokenVerifier
from tuttitrip.shared.config.settings import Settings
from tuttitrip.shared.db.session import get_engine


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
    """Reset the demo account's data (atomic, idempotent).

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
    return await demo_service.run_reset(get_engine(), sub)
