"""MCP server: Auth0 audience, per-tool permissions and the trips tools."""

import uuid
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from tests.shared.tokens import DOMAIN, ROLES_CLAIM, StaticKeySource, make_token
from tuttitrip.main import create_app
from tuttitrip.mcp import api as mcp_api
from tuttitrip.mcp.services import tool_service
from tuttitrip.shared.config.settings import McpSettings, Settings
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access
from tuttitrip.shared.permissions.services import permission_service
from tuttitrip.trips.models import Trip
from tuttitrip.trips.schemas import (
    MemberStatus,
    TripDetails,
    TripListQuery,
    TripRead,
    TripRole,
)
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError

HOST = "tuttitrip-api.gburek.app"
MCP_URL = f"https://{HOST}/api/v1/mcp"
METADATA_URL = f"https://{HOST}/.well-known/oauth-protected-resource/api/v1/mcp"
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Host": HOST,
    "Content-Type": "application/json",
}
USER_GRANTS = [Grant("mcp", Access.READ), Grant("trips", Access.WRITE)]
TRIP_ID = uuid.uuid4()


def settings() -> Settings:
    return Settings(
        auth0={"domain": DOMAIN},
        mcp=McpSettings(enabled=True, resource_url=MCP_URL),
    )


def trip_read() -> TripRead:
    trip = Trip(
        id=TRIP_ID,
        owner_sub="google-oauth2|42",
        name="Gdańsk",
        created_at=datetime(2026, 10, 3, tzinfo=UTC),
        start_date=date(2026, 11, 1),
        end_date=date(2026, 11, 3),
        day_start=time(9),
        day_end=time(19),
        budget_flex_pct=0,
        fairness_alpha=1.0,
    )
    details = TripDetails.model_validate(trip, from_attributes=True)
    return TripRead(
        **details.model_dump(exclude={"kind"}),
        my_role=TripRole.HOST,
        my_status=MemberStatus.CONFIRMED,
    )


@pytest.fixture
def grants(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """The caller's stored grants; also counts how often they are loaded."""
    load = AsyncMock(return_value=(USER_GRANTS, False))
    monkeypatch.setattr(permission_service, "load_access", load)

    @asynccontextmanager
    async def no_session() -> AsyncGenerator[None]:
        yield

    monkeypatch.setattr(tool_service, "open_session", no_session)
    return load


@pytest.fixture
def client(grants: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    assert grants is not None
    monkeypatch.setattr(jwt, "PyJWKClient", lambda *_a, **_k: StaticKeySource())
    with TestClient(create_app(settings())) as test_client:
        yield test_client


def token(**claims: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(aud=MCP_URL, **claims)}"}


def rpc(
    client: TestClient,
    method: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post(
        "/api/v1/mcp", json=body, headers={**HEADERS, **(headers or token())}
    )


def tool_names(client: TestClient, headers: dict[str, str] | None = None) -> list[str]:
    response = rpc(client, "tools/list", headers=headers)
    assert response.status_code == 200, response.text
    return sorted(tool["name"] for tool in response.json()["result"]["tools"])


def call(
    client: TestClient, name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})
    assert response.status_code == 200, response.text
    return response.json()["result"]


# --- authentication ----------------------------------------------------------


def test_no_token_is_401_pointing_at_the_resource_metadata(client: TestClient) -> None:
    response = client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers=HEADERS,
    )
    assert response.status_code == 401
    assert f'resource_metadata="{METADATA_URL}"' in response.headers["www-authenticate"]


def test_a_token_for_the_app_api_is_401(client: TestClient) -> None:
    app_api_token = make_token(aud="https://tuttitrip-api.gburek.app")
    response = rpc(
        client, "tools/list", headers={"Authorization": f"Bearer {app_api_token}"}
    )
    assert response.status_code == 401


def test_a_token_for_another_issuer_is_401(client: TestClient) -> None:
    response = rpc(client, "tools/list", headers=token(iss="https://evil.test/"))
    assert response.status_code == 401


def test_resource_metadata_is_served_at_the_root(client: TestClient) -> None:
    response = client.get(
        "/.well-known/oauth-protected-resource/api/v1/mcp", headers={"Host": HOST}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resource"] == MCP_URL
    assert body["authorization_servers"] == [f"https://{DOMAIN}/"]


def test_a_foreign_host_header_is_refused(client: TestClient) -> None:
    response = rpc(client, "tools/list", headers={**token(), "Host": "evil.test"})
    assert response.status_code in {400, 403, 421}


@pytest.mark.parametrize("version", ["2026-07-28", "2025-06-18", "2025-03-26"])
def test_initialize_works_for_supported_protocol_versions(
    client: TestClient, version: str
) -> None:
    response = rpc(
        client,
        "initialize",
        {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["serverInfo"]["name"] == "TuttiTrip"


# --- permissions per tool ----------------------------------------------------


def test_a_user_sees_the_tools(client: TestClient) -> None:
    assert tool_names(client) == ["get_trip", "list_trips", "whoami"]


def test_without_mcp_read_the_tool_list_is_empty(
    client: TestClient, grants: AsyncMock
) -> None:
    grants.return_value = ([Grant("trips", Access.WRITE)], False)
    assert tool_names(client) == []


def test_without_trips_the_trip_tools_are_hidden(
    client: TestClient, grants: AsyncMock
) -> None:
    grants.return_value = ([Grant("mcp", Access.READ)], False)
    assert tool_names(client) == ["whoami"]


def test_a_blocked_account_sees_no_tools(client: TestClient, grants: AsyncMock) -> None:
    grants.return_value = (USER_GRANTS, True)
    assert tool_names(client) == []


def test_a_hidden_tool_cannot_be_called(client: TestClient, grants: AsyncMock) -> None:
    grants.return_value = ([Grant("trips", Access.WRITE)], False)
    response = rpc(client, "tools/call", {"name": "whoami", "arguments": {}})
    body = response.json()
    assert "error" in body or body["result"]["isError"]


def test_grants_are_loaded_once_per_request(
    client: TestClient, grants: AsyncMock
) -> None:
    tool_names(client)
    assert grants.await_count == 1
    tool_names(client)
    assert grants.await_count == 2  # a new request loads again, never cached


def test_a_superadmin_claim_needs_no_stored_grants(
    client: TestClient, grants: AsyncMock
) -> None:
    grants.return_value = []
    names = tool_names(client, token(**{ROLES_CLAIM: ["admin"]}))
    assert names == ["get_trip", "list_trips", "whoami"]
    grants.assert_not_awaited()


# --- tools -------------------------------------------------------------------


def test_whoami_reports_the_caller_and_permissions(client: TestClient) -> None:
    result = call(client, "whoami")["structuredContent"]
    assert result["sub"] == "google-oauth2|42"
    assert result["is_admin"] is False
    assert result["access"]["mcp"] == "READ"
    assert result["access"]["trips.core"] == "WRITE"


def test_list_trips_returns_a_page(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    lister = AsyncMock(
        return_value=Page[TripRead].of([trip_read()], 1, TripListQuery(size=5))
    )
    monkeypatch.setattr(trip_service, "list_trips", lister)
    page = call(client, "list_trips", {"size": 5})["structuredContent"]
    assert (page["total"], page["size"], page["pages"]) == (1, 5, 1)
    assert page["items"][0]["my_role"] == "host"
    assert lister.call_args.args[1] == "google-oauth2|42"
    assert lister.call_args.args[2].size == 5


def test_get_trip_checks_membership(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    membership = AsyncMock(side_effect=TripNotFoundError(str(TRIP_ID)))
    monkeypatch.setattr(trip_service, "get_membership", membership)
    result = call(client, "get_trip", {"trip_id": str(TRIP_ID)})
    assert result["isError"] is True
    assert result["content"][0]["text"] == mcp_api.TRIP_NOT_FOUND
    assert membership.call_args.args[2:] == ("google-oauth2|42", TripRole.MEMBER)


def test_get_trip_returns_the_trip_to_a_member(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock())
    monkeypatch.setattr(trip_service, "get_trip", AsyncMock(return_value=trip_read()))
    result = call(client, "get_trip", {"trip_id": str(TRIP_ID)})
    assert result["structuredContent"]["id"] == str(TRIP_ID)


# --- wiring ------------------------------------------------------------------


def test_the_server_is_off_by_default() -> None:
    with TestClient(create_app(Settings())) as off:
        assert off.post("/api/v1/mcp", json={}).status_code == 404
        assert (
            off.get("/.well-known/oauth-protected-resource/api/v1/mcp").status_code
            == 404
        )


def test_unknown_api_paths_are_still_404(client: TestClient) -> None:
    assert client.get("/api/v1/nope").status_code == 404
    assert client.get("/api/v1/health/live").status_code == 200


@pytest.mark.parametrize(
    "url", ["http://example.com/api/v1/mcp", "https://x.test/api/v1/mcp/"]
)
def test_resource_url_must_be_https_without_a_trailing_slash(url: str) -> None:
    with pytest.raises(ValueError, match="must"):
        McpSettings(resource_url=url)
