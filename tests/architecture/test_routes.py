"""Every route of the app lives under ``/api/<version>/`` (``API_PREFIX``).

Iterates the effective routes of the real app (``iter_route_contexts`` resolves
FastAPI's lazily included routers), so a router registered without the prefix,
or a FastAPI default (``/docs``, ``/openapi.json``) back at the root, fails here.
Other route-level tests (e.g. permission coverage) should iterate the same way.
"""

from typing import cast

from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from fastmcp.server.http import StarletteWithLifespan
from starlette.applications import Starlette
from starlette.routing import Mount, Route

from tuttitrip.main import API_PREFIX, create_app
from tuttitrip.shared.config.settings import McpSettings, Settings

# RFC 9728 puts the metadata of /api/v1/mcp at the root of the host.
MCP_METADATA_PATH = f"/.well-known/oauth-protected-resource{API_PREFIX}/mcp"
# Unversioned paths that are allowed on purpose: only the MCP resource metadata.
ALLOWED_UNVERSIONED = frozenset({MCP_METADATA_PATH})
# The MCP server is the one app mounted next to the routers, and it holds
# nothing but these two routes (its own paths, relative to the mount).
MCP_MOUNT_PATH = API_PREFIX
MCP_MOUNT_ROUTES = frozenset({"/mcp", MCP_METADATA_PATH})


def mcp_app() -> FastAPI:
    return create_app(Settings(mcp=McpSettings(enabled=True)))


def stray_routes(app: FastAPI) -> list[str]:
    """Routes outside the versioned prefix, except the allow-listed MCP ones.

    A mount is allowed only when it is the single mount, sits at ``API_PREFIX``,
    wraps the FastMCP app and that app serves nothing but ``MCP_MOUNT_ROUTES``.
    """
    stray: list[str] = []
    mounts = 0
    for context in iter_route_contexts(app.routes):
        path = context.path or ""
        route = context.original_route
        if isinstance(route, Mount):
            mounts += 1
            inner = {
                r.path
                for r in cast("Starlette", route.app).routes
                if isinstance(r, Route)
            }
            ok = (
                mounts == 1
                and path == MCP_MOUNT_PATH
                and isinstance(route.app, StarletteWithLifespan)
                and inner == MCP_MOUNT_ROUTES
            )
            if not ok:
                stray.append(f"mount {path}")
        elif not path.startswith(f"{API_PREFIX}/") and path not in ALLOWED_UNVERSIONED:
            stray.append(path)
    return stray


def test_prefix_is_versioned() -> None:
    assert API_PREFIX.startswith("/api/v")
    assert not API_PREFIX.endswith("/")


def test_every_route_is_under_the_versioned_prefix() -> None:
    routes = list(iter_route_contexts(create_app().routes))
    assert any(route.path == f"{API_PREFIX}/health" for route in routes)
    assert stray_routes(create_app()) == []


def test_with_mcp_only_the_metadata_and_one_mount_are_extra() -> None:
    app = mcp_app()
    assert stray_routes(app) == []
    extra = [
        (type(c.original_route).__name__, c.path)
        for c in iter_route_contexts(app.routes)
        if not (c.path or "").startswith(f"{API_PREFIX}/")
    ]
    assert sorted(extra) == [("Mount", MCP_MOUNT_PATH), ("Route", MCP_METADATA_PATH)]


def test_the_stray_route_check_catches_other_routes_and_mounts() -> None:
    app = mcp_app()
    app.get("/elsewhere")(lambda: None)
    app.mount("/other", FastAPI())
    app.mount("/api/v1/second", StarletteWithLifespan(routes=[]))
    assert stray_routes(app) == ["/elsewhere", "mount /other", "mount /api/v1/second"]


def test_a_second_mcp_style_mount_is_refused() -> None:
    app = mcp_app()
    app.mount(API_PREFIX, app.routes[-1].app)  # ty: ignore[unresolved-attribute]
    assert any(item.startswith("mount") for item in stray_routes(app))


def test_openapi_and_docs_are_versioned() -> None:
    app = create_app()
    for url in (app.openapi_url, app.docs_url, app.redoc_url):
        assert url is not None
        assert url.startswith(f"{API_PREFIX}/")


def test_openapi_lists_only_versioned_paths() -> None:
    paths = create_app().openapi()["paths"]
    assert paths
    assert [p for p in paths if not p.startswith(f"{API_PREFIX}/")] == []


def test_old_unversioned_paths_are_gone() -> None:
    with TestClient(create_app()) as client:
        for path in ("/health/live", "/openapi.json", "/docs", "/"):
            assert client.get(path).status_code == 404, path
