"""Every route declares its permission; trip routes also check trip membership.

The checks walk ``create_app().routes`` and each route's dependency tree
(``route.dependant``, which includes router-level dependencies), so they do
not depend on URL prefixes.
"""

import ast
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from tests.architecture.layout import PACKAGE_ROOT, all_modules, rel
from tuttitrip.main import create_app
from tuttitrip.shared.permissions.api import (
    PermissionRequirement,
    PublicMarker,
    api_routes,
    public,
    requires,
    route_markers,
)
from tuttitrip.shared.permissions.registry import Access, Feature, is_leaf
from tuttitrip.trips.api import TripAccess

APP = create_app()
API_ROUTES = api_routes(APP)

# Endpoint function names of the only routes that may be public.
PUBLIC_ENDPOINTS = {"health", "live", "ping", "ping_status"}


def docs_paths(app: FastAPI) -> set[str | None]:
    return {
        app.openapi_url,
        app.docs_url,
        app.redoc_url,
        app.swagger_ui_oauth2_redirect_url,
    }


def uncovered_routes(app: FastAPI) -> list[str]:
    """Routes without exactly one marker (``requires`` or ``public``)."""
    problems: list[str] = []
    for route in iter_route_contexts(app.routes):
        if isinstance(route.original_route, APIRoute):
            markers = route_markers(route)
            if len(markers) != 1:
                methods = ",".join(sorted(route.methods or ()))
                problems.append(f"{methods} {route.path}: {len(markers)} markers")
        elif route.path not in docs_paths(app):
            problems.append(f"{route.path}: not an API route and not docs")
    return problems


def trip_routes_without_access_check(app: FastAPI) -> list[str]:
    return [
        route.path or ""
        for route in api_routes(app)
        if "{trip_id}" in (route.path or "")
        and not any(isinstance(d.call, TripAccess) for d in _dependants(route))
    ]


def _dependants(route: RouteContext) -> list[Dependant]:
    found: list[Dependant] = []
    stack: list[Dependant] = list(route.dependant.dependencies)
    while stack:
        dependant = stack.pop()
        found.append(dependant)
        stack.extend(dependant.dependencies)
    return found


def test_the_app_has_routes_to_check() -> None:
    assert len(API_ROUTES) > 10


def test_every_route_declares_a_permission_or_is_public() -> None:
    assert uncovered_routes(APP) == []


def test_coverage_check_catches_unmarked_and_double_marked_routes() -> None:
    app = FastAPI()
    app.get("/open")(lambda: None)
    app.get(
        "/twice",
        dependencies=[public(), requires(Feature.SEARCH, Access.READ)],
    )(lambda: None)
    router = APIRouter(dependencies=[requires(Feature.SEARCH, Access.READ)])
    router.get("/via-router")(lambda: None)
    app.include_router(router)
    assert uncovered_routes(app) == [
        "GET /open: 0 markers",
        "GET /twice: 2 markers",
    ]


def test_only_the_allow_listed_routes_are_public() -> None:
    public_routes = {
        r.name
        for r in API_ROUTES
        if any(isinstance(m, PublicMarker) for m in route_markers(r))
    }
    assert public_routes == PUBLIC_ENDPOINTS


@pytest.mark.parametrize("route", API_ROUTES, ids=lambda r: f"{r.name}@{r.path}")
def test_requirements_name_leaf_features_from_the_registry(route: RouteContext) -> None:
    for marker in route_markers(route):
        if isinstance(marker, PermissionRequirement):
            assert marker.feature in set(Feature)
            assert is_leaf(marker.feature), f"{route.path} requires group {marker}"


def test_every_trip_route_checks_trip_membership() -> None:
    assert trip_routes_without_access_check(APP) == []


def test_trip_route_check_catches_a_missing_trip_access() -> None:
    app = FastAPI()
    app.get("/trips/{trip_id}/things", dependencies=[public()])(lambda trip_id: trip_id)
    assert trip_routes_without_access_check(app) == ["/trips/{trip_id}/things"]


def _requires_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "requires"
    ]


def _is_member(node: ast.expr, enum: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == enum
    )


@pytest.mark.parametrize(
    "module", [p for p in all_modules() if _requires_calls(p)], ids=rel
)
def test_requires_uses_registry_constants_not_strings(module: Path) -> None:
    for call in _requires_calls(module):
        feature, level = call.args
        assert _is_member(feature, "Feature"), f"line {call.lineno}: use Feature.X"
        assert _is_member(level, "Access"), f"line {call.lineno}: use Access.X"


def test_feature_codes_are_not_spelled_out_elsewhere() -> None:
    codes = {f.value for f in Feature if "." in f.value}
    registry = PACKAGE_ROOT / "shared" / "permissions" / "registry.py"
    offenders = [
        f"{rel(p)}:{node.lineno} {node.value!r}"
        for p in all_modules()
        if p != registry
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and node.value in codes
    ]
    assert offenders == []
