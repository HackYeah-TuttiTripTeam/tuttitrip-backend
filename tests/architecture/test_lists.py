"""Lists rule: a collection endpoint returns `Page[T]`, never a bare `list[...]`.

`LEGACY_UNPAGED` holds the routes that predate the contract
(`tuttitrip.shared.pagination`). It may only shrink: migrate a route to
`Page[T]` and delete its line here. New routes are not allowed on it.
"""

from typing import get_origin

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts

from tuttitrip.main import API_PREFIX, create_app

LEGACY_UNPAGED = frozenset(
    {
        "GET /admin/permissions/audit",
        "GET /admin/permissions/roles",
        "GET /admin/permissions/users",
        "GET /places",
        "GET /places/cities",
        "GET /trips",
        "GET /trips/{trip_id}/expenses",
        "GET /trips/{trip_id}/invitations",
        "GET /trips/{trip_id}/members",
        "GET /trips/{trip_id}/profiles",
        "GET /trips/{trip_id}/profiles/{profile_id}/access-tokens",
        "GET /trips/{trip_id}/ratings",
        "GET /trips/{trip_id}/vetoes",
    }
)
"""`METHOD path` (without the API prefix) of routes that still return a bare list."""


def bare_list_routes(app: FastAPI) -> set[str]:
    """Find the GET routes whose response model is a bare `list[...]`.

    Args:
        app: The application to inspect.

    Returns:
        `GET <path>` keys, path without the API prefix.
    """
    found: set[str] = set()
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if (
            isinstance(route, APIRoute)
            and "GET" in (route.methods or set())
            and get_origin(route.response_model) is list
        ):
            found.add(f"GET {(context.path or '').removeprefix(API_PREFIX)}")
    return found


def test_no_new_endpoint_returns_a_bare_list() -> None:
    new = sorted(bare_list_routes(create_app()) - LEGACY_UNPAGED)
    assert new == [], "Return Page[T] (shared.pagination), see AGENTS.md 'Lists'"


def test_legacy_list_only_shrinks() -> None:
    stale = sorted(LEGACY_UNPAGED - bare_list_routes(create_app()))
    assert stale == [], "Paginated or removed: delete these from LEGACY_UNPAGED"


def test_a_deliberate_violation_is_caught() -> None:
    router = APIRouter()

    @router.get("/things")
    async def list_things() -> list[str]:
        return []

    app = FastAPI()
    app.include_router(router)
    assert bare_list_routes(app) == {"GET /things"}
