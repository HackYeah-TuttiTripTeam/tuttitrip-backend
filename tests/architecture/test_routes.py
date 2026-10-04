"""Every route of the app lives under ``/api/<version>/`` (``API_PREFIX``).

Iterates the effective routes of the real app (``iter_route_contexts`` resolves
FastAPI's lazily included routers), so a router registered without the prefix,
or a FastAPI default (``/docs``, ``/openapi.json``) back at the root, fails here.
Other route-level tests (e.g. permission coverage) should iterate the same way.
"""

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from starlette.routing import Mount, Route

from tuttitrip.main import API_PREFIX, create_app
from tuttitrip.shared.config.settings import McpSettings, Settings

# RFC 9728 puts the metadata of /api/v1/mcp at the root of the host.
MCP_METADATA_PATH = f"/.well-known/oauth-protected-resource{API_PREFIX}/mcp"
# Unversioned paths that are allowed on purpose: only the MCP resource metadata.
ALLOWED_UNVERSIONED = frozenset({MCP_METADATA_PATH})
# The MCP endpoint is a plain route at /api/v1/mcp. Nothing is ever mounted: a
# mount under the prefix would swallow REST 405s and redirects.
MCP_ENDPOINT_PATH = f"{API_PREFIX}/mcp"


def mcp_app() -> FastAPI:
    return create_app(Settings(mcp=McpSettings(enabled=True)))


def stray_routes(app: FastAPI) -> list[str]:
    """Routes outside the versioned prefix (except the MCP metadata) and mounts."""
    stray: list[str] = []
    for context in iter_route_contexts(app.routes):
        path = context.path or ""
        if isinstance(context.original_route, Mount):
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


def test_with_mcp_only_the_endpoint_and_metadata_routes_are_extra() -> None:
    app = mcp_app()
    assert stray_routes(app) == []
    extra = {
        c.path
        for c in iter_route_contexts(app.routes)
        if isinstance(c.original_route, Route)
        and not isinstance(c.original_route, APIRoute)
    }
    assert extra == {MCP_ENDPOINT_PATH, MCP_METADATA_PATH} | docs_like(app)


def docs_like(app: FastAPI) -> set[str | None]:
    return {
        app.openapi_url,
        app.docs_url,
        app.redoc_url,
        app.swagger_ui_oauth2_redirect_url,
    }


def test_the_stray_route_check_catches_other_routes_and_mounts() -> None:
    app = mcp_app()
    app.get("/elsewhere")(lambda: None)
    app.mount("/other", FastAPI())
    app.mount("/api/v1/second", FastAPI())
    assert stray_routes(app) == ["/elsewhere", "mount /other", "mount /api/v1/second"]


def test_rest_keeps_its_method_and_redirect_semantics_with_mcp() -> None:
    with TestClient(mcp_app(), follow_redirects=False) as client:
        assert client.delete(f"{API_PREFIX}/health").status_code == 405
        assert client.get(f"{API_PREFIX}/trips/").status_code == 307


def test_unknown_paths_are_404_in_the_rest_error_format_with_mcp() -> None:
    with TestClient(mcp_app()) as client:
        for path in (f"{API_PREFIX}/nope", f"{API_PREFIX}/mcp/extra", "/nope"):
            response = client.get(path)
            assert response.status_code == 404, path
            assert response.json() == {"detail": "Not Found"}, path


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
