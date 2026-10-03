"""Every route of the app lives under ``/api/<version>/`` (``API_PREFIX``).

Iterates the effective routes of the real app (``iter_route_contexts`` resolves
FastAPI's lazily included routers), so a router registered without the prefix,
or a FastAPI default (``/docs``, ``/openapi.json``) back at the root, fails here.
Other route-level tests (e.g. permission coverage) should iterate the same way.
"""

from fastapi.routing import RouteContext, iter_route_contexts

from tuttitrip.main import API_PREFIX, LEGACY_UNVERSIONED_TAG, create_app

# Unversioned paths that are allowed on purpose (none: the API is all versioned).
ALLOWED_UNVERSIONED = frozenset[str]()


def _is_legacy_alias(route: RouteContext) -> bool:
    # Temporary rollout aliases: hidden from OpenAPI and tagged in main.py.
    return not getattr(
        route, "include_in_schema", True
    ) and LEGACY_UNVERSIONED_TAG in getattr(route, "tags", [])


def test_prefix_is_versioned() -> None:
    assert API_PREFIX.startswith("/api/v")
    assert not API_PREFIX.endswith("/")


def test_every_route_is_under_the_versioned_prefix() -> None:
    routes = list(iter_route_contexts(create_app().routes))
    assert any(route.path == f"{API_PREFIX}/health" for route in routes)
    unversioned = sorted(
        str(route.path)
        for route in routes
        if not (route.path or "").startswith(f"{API_PREFIX}/")
        and route.path not in ALLOWED_UNVERSIONED
        and not _is_legacy_alias(route)
    )
    assert unversioned == []


def test_openapi_and_docs_are_versioned() -> None:
    app = create_app()
    for url in (app.openapi_url, app.docs_url, app.redoc_url):
        assert url is not None
        assert url.startswith(f"{API_PREFIX}/")


def test_openapi_lists_only_versioned_paths() -> None:
    paths = create_app().openapi()["paths"]
    assert paths
    assert [p for p in paths if not p.startswith(f"{API_PREFIX}/")] == []
