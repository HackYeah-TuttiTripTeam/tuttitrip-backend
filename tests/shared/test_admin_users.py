"""``GET /admin/users``: Management API mapping, token cache, permissions."""

import asyncio
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.shared.paths import path
from tests.shared.tokens import admin_bearer, bearer, make_verifier
from tuttitrip.main import create_app
from tuttitrip.shared.admin_users.api import get_management_client
from tuttitrip.shared.admin_users.schemas import UserFilters, UserQuery
from tuttitrip.shared.admin_users.services.management_client import (
    ManagementClient,
    ManagementError,
    build_search,
)
from tuttitrip.shared.auth.api import get_token_verifier
from tuttitrip.shared.config.settings import Auth0Settings
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.api import get_user_grants
from tuttitrip.shared.permissions.logic.resolution import Grant

SECRET = "m2m-secret-for-tests-only"


class Auth0Stub:
    """Fake Auth0 token and users endpoints that record the requests."""

    def __init__(self, users_status: int = 200) -> None:
        self.users_status = users_status
        self.token_calls = 0
        self.user_requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            self.token_calls += 1
            return httpx.Response(
                200, json={"access_token": "mgmt-jwt", "expires_in": 86400}
            )
        self.user_requests.append(request)
        if self.users_status != 200:
            return httpx.Response(self.users_status, json={"message": "nope"})
        return httpx.Response(
            200,
            json={
                "users": [
                    {
                        "user_id": "google-oauth2|1",
                        "email": "ala@example.test",
                        "name": "Ala",
                        "last_login": "2026-10-01T10:00:00.000Z",
                        "created_at": "2026-09-01T10:00:00.000Z",
                        "blocked": True,
                    },
                    {"user_id": "auth0|2", "email": "ola@example.test"},
                ],
                "start": 0,
                "limit": 20,
                "length": 2,
                "total": 42,
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


def _no_grants() -> list[Grant]:
    return []


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
    application.dependency_overrides[get_user_grants] = _no_grants
    application.dependency_overrides[get_management_client] = lambda: client
    return application


@pytest.fixture
def http(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_admin_lists_users_with_block_status_and_last_login(
    http: TestClient, stub: Auth0Stub
) -> None:
    response = http.get(
        path("list_auth0_users"),
        params={
            "q": "Ala",
            "page": 2,
            "size": 20,
            "sort": "last_login",
            "dir": "desc",
            "blocked": "true",
        },
        headers=admin_bearer(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 42
    assert body["pages"] == 3
    assert body["page"] == 2
    first, second = body["items"]
    assert first["sub"] == "google-oauth2|1"
    assert first["provider"] == "google-oauth2"
    assert first["blocked"] is True
    assert first["last_login"].startswith("2026-10-01")
    assert second["blocked"] is False
    assert second["last_login"] is None
    sent = stub.user_requests[0]
    assert sent.headers["authorization"] == "Bearer mgmt-jwt"
    assert sent.url.params["page"] == "1"
    assert sent.url.params["per_page"] == "20"
    assert sent.url.params["sort"] == "last_login:-1"
    assert sent.url.params["q"] == "(email:*ala* OR name:*ala*) AND blocked:true"


def test_token_is_cached_between_requests(http: TestClient, stub: Auth0Stub) -> None:
    for _ in range(2):
        assert (
            http.get(path("list_auth0_users"), headers=admin_bearer()).status_code
            == 200
        )
    assert stub.token_calls == 1


def test_regular_user_gets_403(http: TestClient) -> None:
    response = http.get(path("list_auth0_users"), headers=bearer())
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing permission admin.users:READ"


def test_anonymous_gets_401(http: TestClient) -> None:
    assert http.get(path("list_auth0_users")).status_code == 401


def test_unconfigured_management_api_is_503(app: FastAPI, stub: Auth0Stub) -> None:
    client = make_client(stub, configured=False)
    app.dependency_overrides[get_management_client] = lambda: client
    with TestClient(app) as http:
        response = http.get(path("list_auth0_users"), headers=admin_bearer())
    assert response.status_code == 503
    assert stub.token_calls == 0


def test_auth0_failure_is_502_without_leaking_details(app: FastAPI) -> None:
    client = make_client(Auth0Stub(users_status=429))
    app.dependency_overrides[get_management_client] = lambda: client
    with TestClient(app) as http:
        response = http.get(path("list_auth0_users"), headers=admin_bearer())
    assert response.status_code == 502
    assert SECRET not in response.text


def test_failure_reason_has_no_secret() -> None:
    client = make_client(Auth0Stub(users_status=500))
    with pytest.raises(ManagementError) as caught:
        asyncio.run(client.list_users(UserQuery()))
    assert caught.value.reason == "status_500"
    assert SECRET not in str(caught.value)


def test_search_escapes_lucene_syntax() -> None:
    assert build_search(UserFilters()) is None
    assert build_search(UserFilters(blocked=False)) == "blocked:false"
    assert build_search(UserFilters(q='a" OR x:*')) == (
        '(email:*a\\" or x\\:\\** OR name:*a\\" or x\\:\\**)'
    )
