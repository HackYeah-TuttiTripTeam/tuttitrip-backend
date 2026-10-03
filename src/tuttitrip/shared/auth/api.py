"""HTTP side of auth: ``CurrentUser``/``AdminUser`` dependencies and ``GET /me``."""

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tuttitrip.shared.auth.schemas import AuthenticatedUser, MeResponse
from tuttitrip.shared.auth.services.token_verifier import (
    InvalidTokenError,
    TokenVerifier,
)
from tuttitrip.shared.config.settings import get_settings

router = APIRouter(tags=["auth"])

_bearer = HTTPBearer(auto_error=False)

_NO_BEARER_DETAIL = "Missing bearer token"
_BAD_BEARER_DETAIL = "Invalid token"
_NOT_ADMIN_DETAIL = "Administrator role required"


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


def require_admin(user: CurrentUser) -> AuthenticatedUser:
    """Allow only administrators (``admin`` in the Auth0 roles claim).

    Args:
        user: The authenticated caller.

    Returns:
        The caller, when they are an administrator.
    """
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=_NOT_ADMIN_DETAIL)
    return user


AdminUser = Annotated[AuthenticatedUser, Depends(require_admin)]


@router.get("/me")
def read_me(user: CurrentUser) -> MeResponse:
    """Return the identity behind the access token.

    Args:
        user: The authenticated caller.

    Returns:
        The caller's subject, scopes, permissions and roles.
    """
    return MeResponse(
        sub=user.sub,
        scopes=user.scopes,
        permissions=user.permissions,
        roles=user.roles,
        is_admin=user.is_admin,
    )
