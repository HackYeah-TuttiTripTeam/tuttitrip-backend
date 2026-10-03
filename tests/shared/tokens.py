"""Test doubles for Auth0: a local RSA key, a JWKS stand-in and token factory."""

import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import get_default_algorithms

from tuttitrip.shared.auth.services.token_verifier import TokenVerifier

DOMAIN = "tenant.example.auth0.com"
AUDIENCE = "https://api.example.test"
KID = "test-key"
ROLES_CLAIM = "https://tuttitrip.gburek.app/roles"

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class StaticKeySource:
    """Serves the test public key, like PyJWKClient would from JWKS."""

    def __init__(self) -> None:
        rs256 = get_default_algorithms()["RS256"]
        jwk = rs256.to_jwk(PRIVATE_KEY.public_key(), as_dict=True)
        self.key = jwt.PyJWK({**jwk, "kid": KID, "alg": "RS256", "use": "sig"})

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        assert jwt.get_unverified_header(token)["kid"] == KID
        return self.key


def make_token(**overrides: object) -> str:
    now = int(time.time())
    claims = {
        "iss": f"https://{DOMAIN}/",
        "aud": AUDIENCE,
        "sub": "google-oauth2|42",
        "iat": now,
        "exp": now + 300,
        "scope": "openid profile",
        "permissions": ["read:trips"],
        **overrides,
    }
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": KID})


def make_verifier() -> TokenVerifier:
    return TokenVerifier(DOMAIN, AUDIENCE, key_source=StaticKeySource())


def bearer(**overrides: object) -> dict[str, str]:
    """Authorization header with a valid token (claims overridable)."""
    return {"Authorization": f"Bearer {make_token(**overrides)}"}


def admin_bearer(**overrides: object) -> dict[str, str]:
    """Authorization header for a superadmin (Auth0 roles claim ``admin``)."""
    return bearer(**{ROLES_CLAIM: ["admin"], **overrides})
