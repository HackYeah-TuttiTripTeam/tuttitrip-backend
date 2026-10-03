"""``POST /auth/demo``: the jury's one-link login (public, token in the body)."""

import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from tuttitrip.demo.logic.rate_limit import RateLimiter
from tuttitrip.demo.logic.token import token_matches
from tuttitrip.demo.schemas import DemoLoginRequest, DemoSession
from tuttitrip.demo.services import auth0_login
from tuttitrip.demo.services.auth0_login import DemoLoginError
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.permissions.api import public

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[public()])
log = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "no-store"}
CLIENT_IP_HEADER = "cf-connecting-ip"  # set by Cloudflare in front of the API


@lru_cache(maxsize=1)
def get_rate_limiter() -> RateLimiter:
    """Build the process-wide limiter from the settings.

    Returns:
        The cached limiter.
    """
    return RateLimiter(get_settings().demo.rate_limit_per_minute)


def get_client_factory() -> Callable[[], httpx.AsyncClient]:
    """Provide the way to open the HTTP client for the Auth0 call.

    Returns:
        A factory of clients (tests swap it for one with a mock transport).
    """
    return auth0_login.build_client


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get(CLIENT_IP_HEADER)
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


@router.post(
    "/demo",
    responses={
        404: {"description": "Demo login is off, or the token is wrong."},
        429: {"description": "Too many requests from this address."},
        502: {"description": "Auth0 did not give a token."},
    },
)
async def demo_login(
    body: DemoLoginRequest,
    request: Request,
    response: Response,
    new_client: Annotated[Callable[[], httpx.AsyncClient], Depends(get_client_factory)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> DemoSession:
    """Sign in as the shared demo account with the token from the jury link.

    The link is ``/demo#t=<token>``; the web app reads the fragment and sends
    the token here. Wrong token, missing token and a disabled demo are all the
    same 404. The token and the account's credentials are never logged.

    Args:
        body: The demo token.
        request: Used to find the caller's address for the rate limit.
        response: Used to forbid caching.
        new_client: Opens the HTTP client for Auth0.
        limiter: Per-IP rate limiter.

    Returns:
        Auth0 tokens of the demo account (a regular ``user``).

    Raises:
        HTTPException: 429 over the limit, 404 for a bad token or a disabled
            demo, 502 when Auth0 fails.
    """
    response.headers["Cache-Control"] = NO_STORE["Cache-Control"]
    if not limiter.allow(_client_ip(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests",
            headers={**NO_STORE, "Retry-After": "60"},
        )
    settings = get_settings()
    if not token_matches(body.token, settings.demo.token_sha256):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found", headers=NO_STORE)
    try:
        async with new_client() as client:
            return await auth0_login.login(client, settings.auth0, settings.demo)
    except DemoLoginError as exc:
        log.warning("%s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Demo login unavailable", headers=NO_STORE
        ) from None
