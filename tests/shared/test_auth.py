"""Auth0 token verification and the /me endpoint, with a local RSA key."""

import time
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import get_default_algorithms

from tuttitrip.main import create_app
from tuttitrip.shared.auth.api import get_token_verifier
from tuttitrip.shared.auth.services.token_verifier import (
    InvalidTokenError,
    TokenVerifier,
)

DOMAIN = "tenant.example.auth0.com"
AUDIENCE = "https://api.example.test"
KID = "test-key"

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


@pytest.fixture
def verifier() -> TokenVerifier:
    return TokenVerifier(DOMAIN, AUDIENCE, key_source=StaticKeySource())


def test_valid_token_yields_user(verifier: TokenVerifier) -> None:
    user = verifier.verify(make_token())
    assert user.sub == "google-oauth2|42"
    assert user.scopes == ["openid", "profile"]
    assert user.permissions == ["read:trips"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "https://someone-else"},
        {"iss": "https://evil.example.com/"},
        {"exp": int(time.time()) - 10},
    ],
    ids=["wrong-audience", "wrong-issuer", "expired"],
)
def test_bad_claims_are_rejected(
    verifier: TokenVerifier, overrides: dict[str, Any]
) -> None:
    with pytest.raises(InvalidTokenError):
        verifier.verify(make_token(**overrides))


def test_hs256_token_is_rejected(verifier: TokenVerifier) -> None:
    forged = jwt.encode(
        {"sub": "x"}, "a-shared-secret-of-sufficient-length!", headers={"kid": KID}
    )
    with pytest.raises(InvalidTokenError):
        verifier.verify(forged)


@pytest.fixture
def client(verifier: TokenVerifier) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    with TestClient(app) as test_client:
        yield test_client


def test_me_requires_a_token(client: TestClient) -> None:
    response = client.get("/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_garbage(client: TestClient) -> None:
    response = client.get("/me", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_me_returns_the_caller(client: TestClient) -> None:
    response = client.get("/me", headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 200
    assert response.json() == {
        "sub": "google-oauth2|42",
        "scopes": ["openid", "profile"],
        "permissions": ["read:trips"],
    }


def test_trip_endpoints_require_a_token(client: TestClient) -> None:
    assert client.get("/trips").status_code == 401
