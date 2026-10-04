"""``PATCH /me/account``: name change through Auth0, provider accounts, permissions."""

import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response as Reply

from tests.shared.paths import path
from tests.shared.tokens import bearer, make_verifier
from tuttitrip.accounts.logic.sources import account_source, is_editable
from tuttitrip.accounts.schemas import AccountErrorCode, AccountSource
from tuttitrip.main import create_app
from tuttitrip.shared.admin_users.api import get_management_client
from tuttitrip.shared.admin_users.services.management_client import ManagementClient
from tuttitrip.shared.auth.api import get_token_verifier
from tuttitrip.shared.config.settings import Auth0Settings
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.api import get_user_grants
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature

SECRET = "m2m-secret-for-tests-only"
PASSWORD_SUB = "auth0|abc123"
OTHER_SUB = "auth0|someone-else"


class Auth0Stub:
    """Fake Auth0 token and user-update endpoints that record the requests."""

    def __init__(self, update_status: int = 200) -> None:
        self.update_status = update_status
        self.updates: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(
                200, json={"access_token": "mgmt-jwt", "expires_in": 86400}
            )
        self.updates.append(request)
        if self.update_status != 200:
            return httpx.Response(self.update_status, json={"message": "nope"})
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "user_id": PASSWORD_SUB,
                "email": "ala@example.test",
                "name": body["name"],
            },
        )


def make_client(stub: Auth0Stub, *, configured: bool = True) -> ManagementClient:
    auth0 = Auth0Settings.model_validate(
        {"management_client_id": "cid", "management_client_secret": SECRET}
        if configured
        else {}
    )
    return ManagementClient(
        auth0, httpx.AsyncClient(transport=httpx.MockTransport(stub))
    )


def _user_role_grants() -> list[Grant]:
    return [Grant(Feature.ACCOUNTS_PROFILE.value, Access.WRITE)]


def _read_only_grants() -> list[Grant]:
    return [Grant(Feature.ACCOUNTS_PROFILE.value, Access.READ)]


@pytest.fixture
def stub() -> Auth0Stub:
    return Auth0Stub()


@pytest.fixture
def app(stub: Auth0Stub) -> FastAPI:
    application = create_app()
    verifier = make_verifier()
    client = make_client(stub)
    application.dependency_overrides[get_token_verifier] = lambda: verifier
    application.dependency_overrides[get_session] = lambda: None
    application.dependency_overrides[get_user_grants] = _user_role_grants
    application.dependency_overrides[get_management_client] = lambda: client
    return application


@pytest.fixture
def http(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _patch(http: TestClient, body: object, sub: str = PASSWORD_SUB) -> Reply:
    return http.patch(path("update_my_account"), json=body, headers=bearer(sub=sub))


def test_password_account_rename_reaches_auth0(
    http: TestClient, stub: Auth0Stub
) -> None:
    response = _patch(http, {"name": "  Ala Nowak  "})
    assert response.status_code == 200
    assert response.json() == {
        "sub": PASSWORD_SUB,
        "name": "Ala Nowak",
        "email": "ala@example.test",
        "source": "email",
    }
    (sent,) = stub.updates
    assert sent.method == "PATCH"
    assert sent.url.raw_path == b"/api/v2/users/auth0%7Cabc123"
    assert sent.headers["authorization"] == "Bearer mgmt-jwt"
    assert json.loads(sent.content) == {"name": "Ala Nowak"}


def test_sub_in_body_is_ignored(http: TestClient, stub: Auth0Stub) -> None:
    response = _patch(http, {"name": "Ala", "sub": OTHER_SUB, "user_id": OTHER_SUB})
    assert response.status_code == 200
    assert response.json()["sub"] == PASSWORD_SUB
    (sent,) = stub.updates
    assert sent.url.raw_path == b"/api/v2/users/auth0%7Cabc123"
    assert json.loads(sent.content) == {"name": "Ala"}


@pytest.mark.parametrize(
    ("sub", "source", "provider"),
    [
        ("google-oauth2|42", "google", "Google"),
        ("oauth2|discord|42", "discord", "Discord"),
        ("apple|42", "other", "your login provider"),
    ],
)
def test_provider_account_gets_409_without_calling_auth0(
    http: TestClient, stub: Auth0Stub, sub: str, source: str, provider: str
) -> None:
    response = _patch(http, {"name": "Ala"}, sub=sub)
    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": AccountErrorCode.PROVIDER_MANAGED.value,
            "source": source,
            "message": f"Account data comes from {provider}; change it there.",
        }
    }
    assert stub.updates == []


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "x" * 101,
        "Ala\nNowak",
        "Ala\x7f",
        "Ala\u200bNowak",  # zero-width space
        "Ala\u200fNowak",  # right-to-left mark
        "Ala\u202eNowak",  # right-to-left override
        "Ala\u2066Nowak",  # left-to-right isolate
        "Ala\u2069Nowak",  # pop directional isolate
    ],
)
def test_invalid_name_is_422_without_echo(
    http: TestClient, stub: Auth0Stub, name: str
) -> None:
    response = _patch(http, {"name": name})
    assert response.status_code == 422
    (item,) = response.json()["detail"]
    assert item["loc"] == ["body", "name"]
    assert "input" not in item
    assert stub.updates == []


def test_anonymous_gets_401(http: TestClient) -> None:
    assert (
        http.patch(path("update_my_account"), json={"name": "Ala"}).status_code == 401
    )


def test_read_only_access_gets_403(app: FastAPI, stub: Auth0Stub) -> None:
    app.dependency_overrides[get_user_grants] = _read_only_grants
    with TestClient(app) as http:
        response = _patch(http, {"name": "Ala"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing permission accounts.profile:WRITE"
    assert stub.updates == []


def test_unconfigured_management_api_is_503(app: FastAPI, stub: Auth0Stub) -> None:
    client = make_client(stub, configured=False)
    app.dependency_overrides[get_management_client] = lambda: client
    with TestClient(app) as http:
        assert _patch(http, {"name": "Ala"}).status_code == 503
    assert stub.updates == []


def test_auth0_failure_is_502_without_leaking_details(app: FastAPI) -> None:
    client = make_client(Auth0Stub(update_status=403))
    app.dependency_overrides[get_management_client] = lambda: client
    with TestClient(app) as http:
        response = _patch(http, {"name": "Ala"})
    assert response.status_code == 502
    assert response.json() == {"detail": "Auth0 request failed"}
    assert SECRET not in response.text


@pytest.mark.parametrize(
    ("sub", "source"),
    [
        ("auth0|1", AccountSource.EMAIL),
        ("google-oauth2|1", AccountSource.GOOGLE),
        ("oauth2|discord|1", AccountSource.DISCORD),
        ("oauth2|other|1", AccountSource.OTHER),
        ("auth0", AccountSource.OTHER),
    ],
)
def test_account_source_from_sub(sub: str, source: AccountSource) -> None:
    assert account_source(sub) is source
    assert is_editable(source) is (source is AccountSource.EMAIL)


def test_names_with_diacritics_are_accepted(http: TestClient, stub: Auth0Stub) -> None:
    response = _patch(http, {"name": "Zoë Łęcka-Nowak"})
    assert response.status_code == 200
    assert json.loads(stub.updates[0].content) == {"name": "Zoë Łęcka-Nowak"}
