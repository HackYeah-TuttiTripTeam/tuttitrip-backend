"""Verify Auth0 RS256 access tokens against the tenant's JWKS."""

from typing import Any, Protocol

import jwt

from tuttitrip.shared.auth.schemas import AuthenticatedUser

ALGORITHMS = ["RS256"]


class InvalidTokenError(Exception):
    """The bearer token is missing, malformed, expired or not for this API."""


class SigningKeySource(Protocol):
    """Anything that can resolve the signing key for a JWT (e.g. PyJWKClient)."""

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        """Return the key that signed ``token``."""
        ...


class TokenVerifier:
    """Validate signature, issuer, audience and expiry of Auth0 tokens."""

    def __init__(
        self,
        domain: str,
        audience: str,
        key_source: SigningKeySource | None = None,
    ) -> None:
        self.issuer = f"https://{domain}/"
        self.audience = audience
        self.key_source: SigningKeySource = key_source or jwt.PyJWKClient(
            f"https://{domain}/.well-known/jwks.json", cache_keys=True
        )

    def verify(self, token: str) -> AuthenticatedUser:
        """Decode and validate a bearer token.

        Args:
            token: Raw JWT from the ``Authorization`` header.

        Returns:
            The authenticated user.
        """
        try:
            signing_key = self.key_source.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=ALGORITHMS,
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError(str(exc)) from exc
        scope = claims.get("scope", "")
        return AuthenticatedUser(
            sub=str(claims["sub"]),
            scopes=scope.split() if isinstance(scope, str) else [],
            permissions=[str(p) for p in claims.get("permissions", [])],
        )
