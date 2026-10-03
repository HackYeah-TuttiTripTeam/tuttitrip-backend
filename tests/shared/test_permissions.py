"""Feature registry, permission resolution, ``requires`` and the admin API."""

import asyncio
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests.shared.paths import path
from tests.shared.tokens import admin_bearer, bearer, make_verifier
from tuttitrip.main import create_app
from tuttitrip.shared.auth.api import get_token_verifier
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.api import (
    OPENAPI_PERMISSION_KEY,
    OPENAPI_PUBLIC_KEY,
    PermissionRequirement,
    get_user_grants,
    requires,
)
from tuttitrip.shared.permissions.logic.resolution import (
    Grant,
    beyond_reach,
    invalid_for_default_role,
    resolve,
)
from tuttitrip.shared.permissions.registry import (
    DESCRIPTIONS,
    Access,
    Feature,
    children,
    is_admin_feature,
    is_leaf,
)
from tuttitrip.shared.permissions.services import permission_service
from tuttitrip.shared.permissions.services.permission_service import (
    EscalationError,
    ProtectedRoleError,
    RoleExistsError,
    RoleNotFoundError,
)

R, W = Access.READ, Access.WRITE
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"

# --- registry ----------------------------------------------------------------


def test_access_is_ordered() -> None:
    assert R < W
    assert max(R, W) is W
    assert W.satisfies(R)
    assert W.satisfies(W)
    assert R.satisfies(R)
    assert not R.satisfies(W)


@pytest.mark.parametrize("feature", list(Feature), ids=str)
def test_every_node_has_a_description_and_a_parent(feature: Feature) -> None:
    assert DESCRIPTIONS[feature].strip()
    if feature is Feature.ROOT:
        assert feature.parent is None
    else:
        assert feature.parent is not None  # Feature(...) raises for a missing parent
        assert feature in children(feature.parent)
        assert feature.lineage[-1] is Feature.ROOT


def test_codes_mirror_the_tree() -> None:
    assert Feature.TRIPS_MEMBERS.lineage == (
        Feature.TRIPS_MEMBERS,
        Feature.TRIPS,
        Feature.ROOT,
    )
    assert is_leaf(Feature.TRIPS_MEMBERS)
    assert not is_leaf(Feature.TRIPS)


def test_admin_features_live_under_admin_only() -> None:
    admin = {f for f in Feature if is_admin_feature(f)}
    assert admin == {Feature.ROOT, *(f for f in Feature if f.value.startswith("admin"))}
    assert Feature.ADMIN_PLANNING_WEIGHTS.parent is Feature.ADMIN


# --- resolution --------------------------------------------------------------


def test_group_grant_covers_the_subtree() -> None:
    perms = resolve([Grant("trips", W)])
    for feature in (Feature.TRIPS, Feature.TRIPS_MEMBERS, Feature.TRIPS_INVITATIONS):
        assert perms.allows(feature, W)
        assert perms.allows(feature, R)  # WRITE implies READ
    assert not perms.allows(Feature.PROFILES_CORE, R)


def test_leaf_grant_does_not_climb_up() -> None:
    perms = resolve([Grant("trips.members", W)])
    assert perms.allows(Feature.TRIPS_MEMBERS, W)
    assert not perms.allows(Feature.TRIPS, R)
    assert not perms.allows(Feature.TRIPS_CORE, R)


def test_read_does_not_imply_write() -> None:
    perms = resolve([Grant("search", R)])
    assert perms.allows(Feature.SEARCH, R)
    assert not perms.allows(Feature.SEARCH, W)


def test_highest_level_wins_across_roles_and_direct_grants() -> None:
    # e.g. a role gives trips READ, a direct grant gives trips.members WRITE
    perms = resolve([Grant("trips", R), Grant("trips.members", W), Grant("trips", R)])
    assert perms.level(Feature.TRIPS_MEMBERS) is W
    assert perms.level(Feature.TRIPS_CORE) is R
    assert perms.level(Feature.TRIPS) is R


def test_superadmin_claim_means_write_everywhere() -> None:
    perms = resolve([Grant("search", R)], superadmin=True)
    assert perms.as_dict() == {f.value: W for f in Feature}


def test_unknown_codes_are_ignored_and_denied() -> None:
    perms = resolve([Grant("removed.feature", W), Grant("trip", W)])
    assert perms.as_dict() == {}  # "trip" is not a prefix of "trips"
    assert not perms.allows("removed.feature", R)
    assert perms.level("nope") is None


def test_as_dict_lists_only_accessible_features() -> None:
    assert resolve([Grant("expenses", R)]).as_dict() == {
        "expenses": R,
        "expenses.core": R,
        "expenses.settlement": R,
    }


def test_nobody_grants_above_their_level() -> None:
    actor = resolve([Grant("trips", W), Grant("admin.permissions", R)])
    wanted = [Grant("trips.members", W), Grant("admin.permissions", W), Grant("x", R)]
    assert beyond_reach(actor, wanted) == wanted[1:]


def test_default_role_takes_only_non_admin_leaves() -> None:
    bad = [Grant("trips", W), Grant("admin.users", R), Grant("*", R), Grant("x", R)]
    ok = [Grant("trips.core", W), Grant("search", R)]
    assert invalid_for_default_role([*bad, *ok]) == bad


# --- seeded roles (migration) ----------------------------------------------


def _permissions_migration() -> ModuleType:
    (file,) = MIGRATIONS.glob("*_permissions_*.py")
    spec = importlib.util.spec_from_file_location("permissions_migration", file)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seeded_user_role_is_explicit_leaves_outside_admin() -> None:
    seeded = [
        Grant(f, Access(level))
        for f, level in _permissions_migration().USER_ROLE_GRANTS
    ]
    assert seeded
    assert invalid_for_default_role(seeded) == []
    reachable = resolve(seeded).as_dict()
    assert not any(is_admin_feature(Feature(code)) for code in reachable)
    # Every endpoint a normal user needs is covered by the default role.
    assert reachable[Feature.ACCOUNTS_PROFILE] is W
    assert reachable[Feature.TRIPS_CORE] is W
    assert reachable[Feature.JOBS] is W


# --- requires: dependency behaviour ----------------------------------------


@pytest.mark.parametrize("feature", list(Feature), ids=str)
def test_superadmin_passes_write_on_every_feature(feature: Feature) -> None:
    # New features fall under the superadmin claim automatically.
    requirement = PermissionRequirement(feature, W)
    asyncio.run(requirement(resolve([], superadmin=True)))


def test_requirement_without_access_is_403_naming_the_permission() -> None:
    requirement = PermissionRequirement(Feature.TRIPS_MEMBERS, W)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(requirement(resolve([Grant("trips", R)])))
    assert caught.value.status_code == 403
    assert caught.value.detail == "Missing permission trips.members:WRITE"


def test_superadmin_skips_the_grants_query() -> None:
    admin = AuthenticatedUser(sub="auth0|root", roles=["admin"])
    assert asyncio.run(get_user_grants(admin, session=AsyncMock())) == []


@pytest.fixture
def mini_app() -> FastAPI:
    app = FastAPI()

    @app.get("/members", dependencies=[requires(Feature.TRIPS_MEMBERS, W)])
    def members() -> str:
        return "ok"

    @app.get("/search", dependencies=[requires(Feature.SEARCH, R)])
    def search() -> str:
        return "ok"

    verifier = make_verifier()
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    app.dependency_overrides[get_session] = lambda: None
    return app


def _with_grants(app: FastAPI, *grants: Grant) -> TestClient:
    app.dependency_overrides[get_user_grants] = lambda: list(grants)
    return TestClient(app)


def test_requires_answers_401_without_a_token(mini_app: FastAPI) -> None:
    response = _with_grants(mini_app, Grant("*", W)).get("/members")
    assert response.status_code == 401


def test_requires_answers_403_with_the_missing_permission(mini_app: FastAPI) -> None:
    client = _with_grants(mini_app, Grant("trips", R))
    response = client.get("/members", headers=bearer())
    assert response.status_code == 403
    assert response.json() == {"detail": "Missing permission trips.members:WRITE"}


def test_requires_lets_inherited_access_through(mini_app: FastAPI) -> None:
    client = _with_grants(mini_app, Grant("trips", W), Grant("search", W))
    assert client.get("/members", headers=bearer()).status_code == 200
    assert client.get("/search", headers=bearer()).status_code == 200  # W => R


def test_superadmin_claim_passes_without_any_grant(mini_app: FastAPI) -> None:
    client = _with_grants(mini_app)
    assert client.get("/members", headers=bearer()).status_code == 403
    assert client.get("/members", headers=admin_bearer()).status_code == 200


# --- the real app: /me, OpenAPI, admin API ----------------------------------


@pytest.fixture
def app() -> FastAPI:
    application = create_app()
    verifier = make_verifier()
    application.dependency_overrides[get_token_verifier] = lambda: verifier
    application.dependency_overrides[get_session] = lambda: None
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    app.dependency_overrides[get_user_grants] = lambda: [
        Grant("accounts.profile", W),
        Grant("trips", W),
    ]
    with TestClient(app) as test_client:
        yield test_client


def test_me_returns_identity_and_flattened_permissions(client: TestClient) -> None:
    response = client.get(path("read_me"), headers=bearer())
    assert response.status_code == 200
    assert response.json() == {
        "sub": "google-oauth2|42",
        "scopes": ["openid", "profile"],
        "permissions": ["read:trips"],
        "roles": [],
        "is_admin": False,
        "access": {
            "accounts.profile": "WRITE",
            "trips": "WRITE",
            "trips.core": "WRITE",
            "trips.members": "WRITE",
            "trips.invitations": "WRITE",
        },
    }


def test_me_for_a_superadmin_lists_everything(client: TestClient) -> None:
    body = client.get(path("read_me"), headers=admin_bearer()).json()
    assert body["is_admin"] is True
    assert body["access"] == {f.value: "WRITE" for f in Feature}


def test_me_requires_a_token(client: TestClient) -> None:
    assert client.get(path("read_me")).status_code == 401


def test_openapi_documents_every_requirement(client: TestClient) -> None:
    schema = client.get(path("openapi")).json()
    trips = schema["paths"][path("list_trips")]["get"]
    assert trips[OPENAPI_PERMISSION_KEY] == "trips.core:READ"
    assert "Wymagane uprawnienie: `trips.core:READ`" in trips["description"]
    assert "403" in trips["responses"]
    health = schema["paths"][path("health")]["get"]
    assert health[OPENAPI_PUBLIC_KEY] is True
    assert OPENAPI_PERMISSION_KEY not in health


def test_admin_api_needs_admin_permissions(client: TestClient) -> None:
    response = client.get(path("list_roles"), headers=bearer())
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing permission admin.permissions:READ"


def test_feature_tree_for_admins(client: TestClient) -> None:
    response = client.get(path("list_features"), headers=admin_bearer())
    assert response.status_code == 200
    tree = response.json()
    assert tree["code"] == "*"
    trips = next(c for c in tree["children"] if c["code"] == "trips")
    assert [c["code"] for c in trips["children"]] == [
        "trips.core",
        "trips.members",
        "trips.invitations",
    ]
    assert trips["description"] == "Wyjazdy"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (RoleNotFoundError("ghost"), 404),
        (ProtectedRoleError("Role 'superadmin' comes only from the Auth0 claim"), 403),
        (EscalationError("Cannot grant more than you hold: x:WRITE"), 403),
        (RoleExistsError("ghost"), 409),
    ],
    ids=["not-found", "protected", "escalation", "exists"],
)
def test_admin_errors_map_to_status_codes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
) -> None:
    monkeypatch.setattr(permission_service, "assign_role", AsyncMock(side_effect=error))
    url = path("assign_role", sub="auth0|bob", role="superadmin")
    response = client.put(url, headers=admin_bearer())
    assert response.status_code == status
    assert response.json()["detail"] == str(error)


def test_admin_changes_are_attributed_to_the_caller(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assign = AsyncMock(
        return_value=permission_service.UserPermissionsRead(
            sub="auth0|bob", roles=["planner"], grants=[], effective={}
        )
    )
    monkeypatch.setattr(permission_service, "assign_role", assign)
    url = path("assign_role", sub="auth0|bob", role="planner")
    response = client.put(url, headers=admin_bearer())
    assert response.status_code == 200
    _, actor, sub, role = assign.call_args.args
    assert (actor.sub, sub, role) == ("google-oauth2|42", "auth0|bob", "planner")
    assert actor.permissions.allows(Feature.ADMIN_PERMISSIONS, W)


def test_unknown_feature_code_is_422(client: TestClient) -> None:
    url = path("set_grant", sub="auth0|bob", feature="trips")
    bad = url.replace("/trips", "/nonexistent")
    response = client.put(bad, json={"level": "READ"}, headers=admin_bearer())
    assert response.status_code == 422
