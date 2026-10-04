"""Auth0 token verification and the 401 behaviour, with a local RSA key."""

import time
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from tests.shared.paths import path
from tests.shared.tokens import (
    AUDIENCE,
    DOMAIN,
    KID,
    ROLES_CLAIM,
    StaticKeySource,
    make_token,
    make_verifier,
)
from tuttitrip.main import create_app
from tuttitrip.shared.auth.api import get_token_verifier
from tuttitrip.shared.auth.services.token_verifier import (
    InvalidTokenError,
    TokenVerifier,
)


@pytest.fixture
def verifier() -> TokenVerifier:
    return make_verifier()


def test_valid_token_yields_user(verifier: TokenVerifier) -> None:
    user = verifier.verify(make_token())
    assert user.sub == "google-oauth2|42"
    assert user.scopes == ["openid", "profile"]
    assert user.permissions == ["read:trips"]


def test_user_carries_the_token_expiry(verifier: TokenVerifier) -> None:
    exp = int(time.time()) + 123
    assert verifier.verify(make_token(exp=exp)).exp == exp


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


def test_protected_endpoints_require_a_token(client: TestClient) -> None:
    response = client.get(path("read_me"))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_garbage_token_is_401(client: TestClient) -> None:
    headers = {"Authorization": "Bearer nope"}
    assert client.get(path("list_trips"), headers=headers).status_code == 401


@pytest.mark.parametrize(
    ("claims", "roles"),
    [
        ({}, []),
        ({ROLES_CLAIM: ["admin"]}, ["admin"]),
        ({ROLES_CLAIM: ["viewer"]}, ["viewer"]),
        ({ROLES_CLAIM: "admin"}, []),  # not a list: ignored
        ({"roles": ["admin"]}, []),  # un-namespaced claim: ignored
    ],
    ids=["none", "admin", "other-role", "string-claim", "wrong-claim"],
)
def test_roles_come_from_the_namespaced_claim(
    verifier: TokenVerifier,
    claims: dict[str, object],
    roles: list[str],
) -> None:
    user = verifier.verify(make_token(**claims))
    assert user.roles == roles
    assert user.is_admin is (roles == ["admin"])


def test_roles_claim_name_is_configurable() -> None:
    custom = TokenVerifier(
        DOMAIN, AUDIENCE, key_source=StaticKeySource(), roles_claim="https://x/roles"
    )
    assert custom.verify(make_token(**{"https://x/roles": ["admin"]})).is_admin
    assert not custom.verify(make_token(**{ROLES_CLAIM: ["admin"]})).is_admin
