"""MCP server: Auth0 audience, per-tool permissions and the trips tools."""

import json
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

from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tests.shared.tokens import DOMAIN, ROLES_CLAIM, StaticKeySource, make_token
from tuttitrip.main import create_app
from tuttitrip.mcp import api as mcp_api
from tuttitrip.mcp import schemas as constants
from tuttitrip.mcp.services import tool_service
from tuttitrip.planning.linter.logic import trip_lint
from tuttitrip.planning.linter.schemas import LintPlan, LintRequest
from tuttitrip.planning.linter.services import linter_service
from tuttitrip.planning.plans.logic.sample_plan import sample_plan
from tuttitrip.planning.plans.schemas import PlanRead
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.plans.services.plan_service import PlanNotFoundError
from tuttitrip.planning.schemas import PlanningInput
from tuttitrip.profiles.feedback.schemas import VetoRead
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.profiles.feedback.services.feedback_service import FeedbackForbiddenError
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.config.settings import McpSettings, Settings
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access
from tuttitrip.shared.permissions.services import permission_service
from tuttitrip.shared.rate_limit.limiter import RateLimiter
from tuttitrip.trips.models import Trip
from tuttitrip.trips.schemas import (
    MemberStatus,
    TripDetails,
    TripListQuery,
    TripMembership,
    TripRead,
    TripRole,
)
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError, TripRoleError

HOST = "tuttitrip-api.gburek.app"
MCP_URL = f"https://{HOST}/api/v1/mcp"
METADATA_URL = f"https://{HOST}/.well-known/oauth-protected-resource/api/v1/mcp"
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Host": HOST,
    "Content-Type": "application/json",
}
USER_GRANTS = [
    Grant("mcp", Access.READ),
    Grant("trips", Access.WRITE),
    Grant("planning.plans", Access.READ),
    Grant("planning.fairness", Access.READ),
    Grant("planning.linter", Access.READ),
    Grant("profiles.feedback", Access.WRITE),
]
ALL_TOOLS = [
    "get_fairness",
    "get_plan",
    "get_trip",
    "get_violations",
    "lint_plan",
    "list_trips",
    "rate_place",
    "veto_place",
    "whoami",
]
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
        propose_cheaper_alternatives=True,
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
    assert tool_names(client) == ALL_TOOLS


def test_without_planning_fairness_the_ledger_tool_is_hidden(
    client: TestClient, grants: AsyncMock
) -> None:
    grants.return_value = (
        [g for g in USER_GRANTS if g.feature != "planning.fairness"],
        False,
    )
    assert "get_fairness" not in tool_names(client)
    assert "get_plan" in tool_names(client)


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
    grants.return_value = ([], False)
    names = tool_names(client, token(**{ROLES_CLAIM: ["admin"]}))
    assert names == ALL_TOOLS
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


# --- plan, ledger and violations --------------------------------------------


def member_of(role: TripRole = TripRole.MEMBER) -> TripMembership:
    return TripMembership(trip_id=TRIP_ID, sub="google-oauth2|42", role=role)


@pytest.fixture
def membership(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    found = AsyncMock(return_value=member_of())
    monkeypatch.setattr(trip_service, "get_membership", found)
    return found


@pytest.fixture
def stored_plan(monkeypatch: pytest.MonkeyPatch) -> PlanRead:
    plan = sample_plan(TRIP_ID)
    monkeypatch.setattr(plan_service, "latest_plan", AsyncMock(return_value=plan))
    return plan


def test_get_plan_returns_the_days_with_hours_and_verification(
    client: TestClient, membership: AsyncMock, stored_plan: PlanRead
) -> None:
    result = call(client, "get_plan", {"trip_id": str(TRIP_ID)})["structuredContent"]
    assert result["day_count"] == len(stored_plan.days)
    stop = result["days"][0]["items"][0]
    assert {"start", "end", "price_verified", "hours_verified"} <= stop.keys()
    assert result["plan_hash"] == stored_plan.plan_hash
    assert "explain" not in result
    assert membership.call_args.args[3] is TripRole.MEMBER


@pytest.mark.usefixtures("membership")
def test_get_plan_one_day(client: TestClient, stored_plan: PlanRead) -> None:
    result = call(client, "get_plan", {"trip_id": str(TRIP_ID), "day": 2})
    days = result["structuredContent"]["days"]
    assert [d["index"] for d in days] == [2]
    assert result["structuredContent"]["day_count"] == len(stored_plan.days)


def test_get_plan_for_a_day_the_plan_lacks_is_an_error(
    client: TestClient, membership: AsyncMock, stored_plan: PlanRead
) -> None:
    assert stored_plan.days
    result = call(client, "get_plan", {"trip_id": str(TRIP_ID), "day": 99})
    assert result["isError"] is True
    assert result["content"][0]["text"] == constants.DAY_OUT_OF_RANGE
    assert membership.await_count == 1


@pytest.mark.usefixtures("membership")
def test_get_fairness_equals_the_numbers_of_the_plan_endpoint(
    client: TestClient, stored_plan: PlanRead
) -> None:
    result = call(client, "get_fairness", {"trip_id": str(TRIP_ID)})
    fairness = result["structuredContent"]["fairness"]
    assert fairness == stored_plan.fairness.model_dump(mode="json")
    person = fairness["per_person"][0]
    assert {"u", "u_star", "r", "floor_eff"} <= person.keys()
    assert len(person["domains"]) == 5
    text = json.dumps(result["structuredContent"])
    assert "email" not in text
    assert "stairs" not in text


@pytest.mark.parametrize("error", [TripNotFoundError("x"), TripRoleError("x")])
def test_a_stranger_gets_one_message_without_details(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=error))
    for name in ("get_fairness", "get_plan", "get_violations"):
        result = call(client, name, {"trip_id": str(TRIP_ID)})
        assert result["isError"] is True
        assert result["content"][0]["text"] == constants.TRIP_NOT_FOUND


def test_a_trip_without_a_plan_says_so(
    client: TestClient, membership: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        plan_service, "latest_plan", AsyncMock(side_effect=PlanNotFoundError("x"))
    )
    result = call(client, "get_fairness", {"trip_id": str(TRIP_ID)})
    assert result["content"][0]["text"] == constants.NO_PLAN
    assert membership.await_count == 1


def planning_of_the_family() -> tuple[PlanningInput, dict[uuid.UUID, str], float]:
    data = planning_input(reference(), lodging=False)
    return data, {p.id: "Ktoś" for p in data.people}, 1.0


NAMED_ITEMS = [
    {"name": "Nieistniejący Park Wodny", "start": "09:00", "end": "11:00"},
    {"name": "Kolejny Wymysł", "start": "11:05", "end": "12:00"},
]
NAMED_PLAN = {"day": "2026-10-09", "items": NAMED_ITEMS}


@pytest.mark.usefixtures("membership")
def test_lint_plan_counts_the_same_as_the_linter_endpoint(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    planning, names, alpha = planning_of_the_family()
    monkeypatch.setattr(
        plan_service, "gather_input", AsyncMock(return_value=(planning, names, alpha))
    )
    result = call(
        client, "lint_plan", {"trip_id": str(TRIP_ID), "plan": {"days": [NAMED_PLAN]}}
    )["structuredContent"]
    expected = linter_service.check_plan(
        LintRequest(
            plan=LintPlan.model_validate(
                {
                    "days": [
                        {
                            "day": NAMED_PLAN["day"],
                            "items": [
                                {**item, "place_id": None} for item in NAMED_ITEMS
                            ],
                        }
                    ]
                }
            ),
            context=trip_lint.context_of(planning, names),
        )
    )
    assert result["count"] == expected.count > 0
    assert result["digest"] == expected.digest
    unknown = next(r for r in result["results"] if r["rule"] == "unknown_place")
    assert unknown["count"] == 2


# --- tools that change data --------------------------------------------------


@pytest.mark.usefixtures("membership")
def test_veto_reports_what_left_the_plan(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = sample_plan(TRIP_ID)
    gone = before.days[0].items[0]
    after = before.model_copy(
        update={
            "version": before.version + 1,
            "days": [
                day.model_copy(update={"items": [i for i in day.items if i != gone]})
                for day in before.days
            ],
        }
    )
    profile = uuid.uuid4()
    veto = VetoRead(
        id=uuid.uuid4(),
        trip_id=TRIP_ID,
        profile_id=profile,
        place_id=gone.place_id,
        created_by_sub="google-oauth2|42",
        on_behalf=False,
        created_at=datetime(2026, 10, 4, tzinfo=UTC),
        revoked_at=None,
        revoked_by_sub=None,
    )
    monkeypatch.setattr(plan_service, "latest_plan", AsyncMock(return_value=before))
    monkeypatch.setattr(
        plan_service, "generate_plan", AsyncMock(return_value=(after, True))
    )
    monkeypatch.setattr(
        profile_service, "find_account_profile", AsyncMock(return_value=profile)
    )
    create = AsyncMock(return_value=veto)
    monkeypatch.setattr(feedback_service, "create_veto", create)
    result = call(
        client, "veto_place", {"trip_id": str(TRIP_ID), "place_id": str(gone.place_id)}
    )["structuredContent"]
    assert result["removed"] == [gone.name]
    assert result["plan_version"] == after.version
    assert result["veto"]["created_by_sub"] == "google-oauth2|42"
    assert create.call_args.args[2].profile_id == profile


@pytest.mark.usefixtures("membership")
def test_a_member_cannot_veto_for_somebody_else(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        plan_service, "latest_plan", AsyncMock(side_effect=PlanNotFoundError("x"))
    )
    monkeypatch.setattr(
        feedback_service,
        "create_veto",
        AsyncMock(side_effect=FeedbackForbiddenError("no")),
    )
    result = call(
        client,
        "veto_place",
        {
            "trip_id": str(TRIP_ID),
            "place_id": str(uuid.uuid4()),
            "on_behalf_of": str(uuid.uuid4()),
        },
    )
    assert result["isError"] is True
    assert result["content"][0]["text"] == constants.NOT_ALLOWED


@pytest.mark.usefixtures("membership")
def test_write_calls_over_the_limit_are_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    limiter = RateLimiter(2)
    monkeypatch.setattr(tool_service, "write_limiter", lambda: limiter)
    monkeypatch.setattr(
        profile_service, "find_account_profile", AsyncMock(return_value=uuid.uuid4())
    )
    monkeypatch.setattr(feedback_service, "rate_place", AsyncMock(return_value=None))
    args = {"trip_id": str(TRIP_ID), "place_id": str(uuid.uuid4()), "rating": "want"}
    results = [call(client, "rate_place", args) for _ in range(3)]
    assert [r.get("isError", False) for r in results[:2]] == [False, False]
    assert results[2]["isError"] is True
    assert results[2]["content"][0]["text"] == constants.RATE_LIMITED


def test_write_tools_carry_the_right_hints(client: TestClient) -> None:
    tools = {
        t["name"]: t["annotations"]
        for t in rpc(client, "tools/list").json()["result"]["tools"]
    }
    assert tools["get_plan"]["readOnlyHint"] is True
    assert tools["get_plan"]["openWorldHint"] is False
    assert tools["rate_place"]["readOnlyHint"] is False
    assert tools["rate_place"]["idempotentHint"] is True
    assert tools["veto_place"]["destructiveHint"] is True


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
