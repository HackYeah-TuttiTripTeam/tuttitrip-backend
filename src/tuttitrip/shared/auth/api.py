"""HTTP side of authentication: the ``CurrentUser`` dependency.

Authorization (``requires``, ``GET /me``) lives in ``shared.permissions``.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.auth.services.token_verifier import (
    InvalidTokenError,
    TokenVerifier,
)
from tuttitrip.shared.config.settings import get_settings

_bearer = HTTPBearer(auto_error=False)

_NO_BEARER_DETAIL = "Missing bearer token"
_BAD_BEARER_DETAIL = "Invalid token"


class UnauthorizedError(HTTPException):
    """401 response carrying the ``WWW-Authenticate: Bearer`` challenge."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


@lru_cache(maxsize=1)
def get_token_verifier() -> TokenVerifier:
    """Build the verifier for the configured Auth0 tenant.

    Returns:
        A cached verifier (its JWKS client caches keys).
    """
    auth0 = get_settings().auth0
    return TokenVerifier(
        domain=auth0.domain, audience=auth0.audience, roles_claim=auth0.roles_claim
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> AuthenticatedUser:
    """Resolve the caller from the bearer token.

    Sync on purpose: FastAPI runs it in a threadpool, so the blocking JWKS
    fetch never stalls the event loop.

    Args:
        credentials: Parsed ``Authorization: Bearer`` header, if any.
        verifier: Token verifier for the configured tenant.

    Returns:
        The authenticated user.
    """
    if credentials is None:
        raise UnauthorizedError(_NO_BEARER_DETAIL)
    try:
        return verifier.verify(credentials.credentials)
    except InvalidTokenError as exc:
        raise UnauthorizedError(_BAD_BEARER_DETAIL) from exc


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
