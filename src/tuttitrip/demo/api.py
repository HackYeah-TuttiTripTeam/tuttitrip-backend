"""``POST /auth/demo``: the jury's one-link login (public, token in the body).

Every way of asking wrongly (no body, no token, an empty, oversized, wrong or
disabled one) is the same 404 with ``Cache-Control: no-store``: nothing tells
a guesser which part was wrong. The limiter runs before the body is read.
"""

import json
import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from tuttitrip.demo.logic.rate_limit import RateLimiter
from tuttitrip.demo.logic.token import secret_matches, token_matches
from tuttitrip.demo.schemas import DemoResetResult, DemoSession
from tuttitrip.demo.services import auth0_login, reset_service
from tuttitrip.demo.services.auth0_login import DemoLoginError
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.permissions.api import public

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[public()])
# Not reachable from outside: the gateway answers 404 for /api/v1/internal/ and
# the worker calls the API container directly on the Docker network.
internal_router = APIRouter(
    prefix="/internal/demo", tags=["internal"], dependencies=[public()]
)
log = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "no-store"}
MAX_BODY_BYTES = 2048
MAX_TOKEN_CHARS = 512
BODY_SCHEMA = {
    "required": True,
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"token": {"type": "string"}},
                "required": ["token"],
                "title": "DemoLoginRequest",
            }
        }
    },
}


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
    # The socket peer, as resolved by uvicorn: X-Forwarded-For is honoured only
    # from the gateway (FORWARDED_ALLOW_IPS), which sets it from CF-Connecting-IP.
    return request.client.host if request.client else "unknown"


async def _read_token(request: Request) -> str | None:
    """Read the token from a small JSON body.

    Returns:
        The token, or None for anything off (size, JSON, shape, length).
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not 0 < len(token) <= MAX_TOKEN_CHARS:
        return None
    return token


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found", headers=NO_STORE)


@router.post(
    "/demo",
    openapi_extra={"requestBody": BODY_SCHEMA},
    responses={
        404: {"description": "Demo login is off, or the request/token is wrong."},
        429: {"description": "Too many requests from this address."},
        502: {"description": "Auth0 did not give a token."},
    },
)
async def demo_login(
    request: Request,
    response: Response,
    new_client: Annotated[Callable[[], httpx.AsyncClient], Depends(get_client_factory)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> DemoSession:
    """Sign in as the shared demo account with the token from the jury link.

    The link is ``/demo#t=<token>``; the web app reads the fragment and sends
    ``{"token": "..."}`` here. Any bad request, a wrong token and a disabled
    demo are the same 404 (no 422). The token and the account's credentials
    are never logged.

    Args:
        request: The raw request (the body is parsed here, after the limiter).
        response: Used to forbid caching.
        new_client: Opens the HTTP client for Auth0.
        limiter: Per-IP rate limiter.

    Returns:
        Auth0 tokens of the demo account (a regular ``user``).

    Raises:
        HTTPException: 429 over the limit, 404 for a bad request or token or a
            disabled demo, 502 when Auth0 fails.
    """
    response.headers["Cache-Control"] = NO_STORE["Cache-Control"]
    if not limiter.allow(_client_ip(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests",
            headers={**NO_STORE, "Retry-After": "60"},
        )
    settings = get_settings()
    token = await _read_token(request)
    if token is None or not token_matches(token, settings.demo.token_sha256):
        raise _not_found()
    try:
        async with new_client() as client:
            return await auth0_login.login(client, settings.auth0, settings.demo)
    except DemoLoginError as exc:
        log.warning("Demo login failed: reason=%s", exc.reason)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Demo login unavailable", headers=NO_STORE
        ) from None


def _bearer(request: Request) -> str:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value if scheme.lower() == "bearer" else ""


@internal_router.post("/reset", include_in_schema=False)
async def reset_demo(
    request: Request,
    response: Response,
    new_client: Annotated[Callable[[], httpx.AsyncClient], Depends(get_client_factory)],
) -> DemoResetResult:
    """Reset the demo account's data (called by the worker's daily schedule).

    Guarded by the shared bearer secret ``TUTTITRIP_DEMO__RESET_SECRET``; a
    missing or wrong secret and an unset one are the same 404. The reset is
    the deploy command's: atomic, idempotent, serialized by an advisory lock.

    Args:
        request: The raw request (for the bearer secret).
        response: Used to forbid caching.
        new_client: Opens the HTTP client for Auth0.

    Returns:
        ``reset`` with the number of trips, or ``disabled`` when the demo is off.

    Raises:
        HTTPException: 404 for a wrong, missing or unconfigured secret, 502 when
            Auth0 fails.
    """
    response.headers["Cache-Control"] = NO_STORE["Cache-Control"]
    settings = get_settings()
    if not secret_matches(
        _bearer(request), settings.demo.reset_secret.get_secret_value()
    ):
        raise _not_found()
    try:
        trips = await reset_service.reset_demo(settings, new_client)
    except DemoLoginError as exc:
        log.warning("Demo reset failed: reason=%s", exc.reason)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Demo login unavailable", headers=NO_STORE
        ) from None
    if trips is None:
        return DemoResetResult(status="disabled")
    return DemoResetResult(status="reset", trips=trips)
