"""Block, unblock and delete accounts (Auth0 stubbed)."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tests.shared.tokens import admin_bearer, bearer, make_verifier
from tuttitrip.main import create_app
from tuttitrip.shared.admin_users.api import get_management_client
from tuttitrip.shared.admin_users.services import erasure
from tuttitrip.shared.admin_users.services.management_client import ManagementClient
from tuttitrip.shared.auth.api import get_token_verifier
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.config.settings import Auth0Settings, get_settings
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.shared.permissions.services import permission_service

ADMIN = AuthenticatedUser(sub="auth0|admin")
TARGET = "google-oauth2|target"
ENCODED = "google-oauth2%7Ctarget"


class Auth0Stub:
    """Fake Auth0: token, patch and delete; records the calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.fail: int | None = None
        self.missing = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        raw = request.url.raw_path.decode()
        body = request.content.decode() if request.content else None
        self.calls.append((request.method, raw, body))
        if self.missing:
            return httpx.Response(404, json={})
        if self.fail:
            return httpx.Response(self.fail, json={})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={})

    def mutations(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] in {"PATCH", "DELETE"}]


def _client(stub: Auth0Stub, *, configured: bool = True) -> ManagementClient:
    auth0 = Auth0Settings.model_validate(
        {"management_client_id": "cid", "management_client_secret": "s"}
        if configured
        else {}
    )
    return ManagementClient(
        auth0, httpx.AsyncClient(transport=httpx.MockTransport(stub))
    )


@pytest.fixture
def stub() -> Auth0Stub:
    return Auth0Stub()


@pytest.fixture
def session() -> MagicMock:
    mock = MagicMock()
    mock.execute = AsyncMock(return_value=MagicMock(all=list))
    mock.commit = AsyncMock()
    return mock


def _app(stub: Auth0Stub, session: MagicMock, *, configured: bool = True) -> FastAPI:
    app = create_app()
    client = _client(stub, configured=configured)
    authorize(app, ADMIN)
    app.dependency_overrides[get_management_client] = lambda: client
    app.dependency_overrides[get_session] = lambda: session
    return app


@pytest.fixture
def http(stub: Auth0Stub, session: MagicMock) -> Iterator[TestClient]:
    with TestClient(_app(stub, session)) as client:
        yield client


def test_block_changes_auth0_and_records_it(
    http: TestClient, stub: Auth0Stub, session: MagicMock
) -> None:
    response = http.post(path("block_user", sub=TARGET))
    assert response.status_code == 204
    assert stub.mutations() == [
        ("PATCH", f"/api/v2/users/{ENCODED}", '{"blocked":true}')
    ]
    session.commit.assert_awaited_once()
    actions = [c.args[0].action for c in session.add.call_args_list]
    assert actions == ["user.block"]


def test_unblock_lifts_both_sides(
    http: TestClient, stub: Auth0Stub, session: MagicMock
) -> None:
    assert http.delete(path("unblock_user", sub=TARGET)).status_code == 204
    assert stub.mutations()[0][2] == '{"blocked":false}'
    assert session.add.call_args.args[0].action == "user.unblock"


def test_admin_cannot_block_or_delete_self(
    http: TestClient, stub: Auth0Stub, session: MagicMock
) -> None:
    assert http.post(path("block_user", sub=ADMIN.sub)).status_code == 409
    assert http.delete(path("delete_user", sub=ADMIN.sub)).status_code == 409
    assert stub.mutations() == []
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("sub", ["oauth2|discord|1234567890", "discord|1234567890"])
def test_protected_discord_ids_cannot_be_blocked_or_deleted(
    http: TestClient,
    stub: Auth0Stub,
    session: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    sub: str,
) -> None:
    monkeypatch.setenv("TUTTITRIP_ADMIN__PROTECTED_DISCORD_IDS", '["1234567890"]')
    get_settings.cache_clear()
    try:
        assert http.post(path("block_user", sub=sub)).status_code == 409
        assert http.delete(path("delete_user", sub=sub)).status_code == 409
    finally:
        get_settings.cache_clear()
    assert stub.mutations() == []
    session.commit.assert_not_awaited()


def test_other_discord_accounts_and_lookalikes_are_not_protected(
    http: TestClient, stub: Auth0Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TUTTITRIP_ADMIN__PROTECTED_DISCORD_IDS", '["1234567890"]')
    get_settings.cache_clear()
    try:
        for sub in (
            "oauth2|discord|999",
            "google-oauth2|1234567890",
            "auth0|1234567890",
        ):
            assert http.post(path("block_user", sub=sub)).status_code == 204
    finally:
        get_settings.cache_clear()
    assert len(stub.mutations()) == 3


def test_nobody_is_protected_by_default(http: TestClient) -> None:
    assert http.post(path("block_user", sub="oauth2|discord|1")).status_code == 204


def test_auth0_failure_leaves_our_block_in_place(
    http: TestClient, stub: Auth0Stub, session: MagicMock
) -> None:
    stub.fail = 500
    assert http.post(path("block_user", sub=TARGET)).status_code == 502
    session.commit.assert_awaited_once()
    assert [c.args[0].action for c in session.add.call_args_list] == ["user.block"]


def test_unblock_failure_in_auth0_changes_nothing_here(
    http: TestClient, stub: Auth0Stub, session: MagicMock
) -> None:
    stub.fail = 500
    assert http.delete(path("unblock_user", sub=TARGET)).status_code == 502
    session.commit.assert_not_awaited()


def test_unknown_account_is_404(
    http: TestClient, stub: Auth0Stub, session: MagicMock
) -> None:
    stub.missing = True
    assert http.post(path("block_user", sub=TARGET)).status_code == 404
    # the block written first is lifted again
    assert [c.args[0].action for c in session.add.call_args_list] == [
        "user.block",
        "user.unblock",
    ]
    session.add.reset_mock()
    assert http.delete(path("unblock_user", sub=TARGET)).status_code == 404
    session.add.assert_not_called()


def test_unconfigured_management_api_is_503(
    stub: Auth0Stub, session: MagicMock
) -> None:
    with TestClient(_app(stub, session, configured=False)) as client:
        assert client.post(path("block_user", sub=TARGET)).status_code == 503
        assert client.delete(path("delete_user", sub=TARGET)).status_code == 503


def test_block_needs_write_on_admin_users(stub: Auth0Stub, session: MagicMock) -> None:
    app = _app(stub, session)
    authorize(app, ADMIN, [Grant(Feature.ADMIN_USERS, Access.READ)])
    with TestClient(app) as client:
        assert client.post(path("block_user", sub=TARGET)).status_code == 403
        assert client.delete(path("delete_user", sub=TARGET)).status_code == 403


def test_delete_removes_in_auth0_cleans_data_and_keeps_the_block(
    http: TestClient,
    stub: Auth0Stub,
    session: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    erase = AsyncMock(return_value={"trips_deleted": 2, "trips_handed_over": 1})
    monkeypatch.setattr(erasure, "erase", erase)
    assert http.delete(path("delete_user", sub=TARGET)).status_code == 204
    assert stub.mutations() == [("DELETE", f"/api/v2/users/{ENCODED}", None)]
    erase.assert_awaited_once_with(session, TARGET)
    entries = [c.args[0] for c in session.add.call_args_list]
    assert [e.action for e in entries] == ["user.block", "user.delete"]
    assert entries[1].change == {
        "trips_deleted": 2,
        "trips_handed_over": 1,
        "access_tokens_revoked": 0,
    }


def test_delete_of_an_account_already_gone_still_cleans_up(
    http: TestClient, stub: Auth0Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub.missing = True
    erase = AsyncMock()
    monkeypatch.setattr(erasure, "erase", erase)
    assert http.delete(path("delete_user", sub=TARGET)).status_code == 204
    erase.assert_awaited_once()


def test_registered_erasers_all_run() -> None:
    first = AsyncMock(return_value={"a": 1})
    second = AsyncMock(return_value={"b": 2})
    saved = list(erasure._ERASERS)  # ruff: ignore[private-member-access]  # a registry test needs a clean slate
    erasure._ERASERS.clear()  # ruff: ignore[private-member-access]
    try:
        erasure.register(first)
        erasure.register(first)
        erasure.register(second)
        session = MagicMock()
        import asyncio  # ruff: ignore[import-outside-top-level]

        counts = asyncio.run(erasure.erase(session, "auth0|x"))
        assert counts == {"a": 1, "b": 2}
        first.assert_awaited_once_with(session, "auth0|x")
        second.assert_awaited_once_with(session, "auth0|x")
    finally:
        erasure._ERASERS[:] = saved  # ruff: ignore[private-member-access]


def test_blocked_account_gets_403_with_a_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        permission_service, "load_access", AsyncMock(return_value=([], True))
    )
    app = create_app()
    verifier = make_verifier()
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app) as client:
        response = client.get(path("read_me"), headers=bearer())
    assert response.status_code == 403
    assert response.json()["detail"] == "Konto zablokowane"


def test_active_account_passes_the_block_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grants = [Grant(Feature.ACCOUNTS_PROFILE, Access.READ)]
    monkeypatch.setattr(
        permission_service, "load_access", AsyncMock(return_value=(grants, False))
    )
    app = create_app()
    verifier = make_verifier()
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app) as client:
        assert client.get(path("read_me"), headers=bearer()).status_code == 200


def test_blocked_superadmin_claim_is_refused_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(permission_service, "is_blocked", AsyncMock(return_value=True))
    app = create_app()
    verifier = make_verifier()
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app) as client:
        response = client.get(path("read_me"), headers=admin_bearer())
    assert response.status_code == 403
