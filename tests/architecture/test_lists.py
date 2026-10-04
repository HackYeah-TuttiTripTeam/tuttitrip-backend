"""Lists rule: a collection endpoint returns `Page[T]`, never a bare `list[...]`.

`LEGACY_UNPAGED` holds the routes that predate the contract
(`tuttitrip.shared.pagination`). It may only shrink: migrate a route to
`Page[T]` and delete its line here. New routes are not allowed on it.
"""

from collections.abc import Sequence
from types import UnionType
from typing import Annotated, Union, get_args, get_origin, get_type_hints

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts

from tuttitrip.main import API_PREFIX, create_app
from tuttitrip.shared.pagination.schemas import Page

LEGACY_UNPAGED = frozenset(
    {
        "GET /admin/permissions/audit",
        "GET /admin/permissions/roles",
        "GET /admin/permissions/users",
        "GET /places",
        "GET /places/cities",
        "GET /trips/{trip_id}/invitations",
        "GET /trips/{trip_id}/members",
        "GET /trips/{trip_id}/preferences",
        "GET /trips/{trip_id}/profiles",
        "GET /trips/{trip_id}/profiles/{profile_id}/access-tokens",
        "GET /trips/{trip_id}/ratings",
        "GET /trips/{trip_id}/vetoes",
    }
)
"""`METHOD path` (without the API prefix) of routes that still return a bare list."""


COLLECTIONS = (list, Sequence, set, frozenset, tuple)


def is_bare_collection(annotation: object) -> bool:
    """Tell whether an annotation is (or contains) a bare collection.

    Unwraps `Annotated[...]`, `X | None` / `Optional` / unions and
    `Sequence`/`set`/`tuple` aliases. Not followed: collections hidden behind a
    custom class or a `RootModel`; those need a review, not this test.

    Args:
        annotation: A return annotation or response model.

    Returns:
        True when a response would be a JSON array.
    """
    origin = get_origin(annotation)
    if origin is Annotated:
        return is_bare_collection(get_args(annotation)[0])
    if origin is Union or origin is UnionType:
        return any(is_bare_collection(arg) for arg in get_args(annotation))
    return (origin or annotation) in COLLECTIONS


def bare_list_routes(app: FastAPI) -> set[str]:
    """Find the GET routes that answer with a bare collection.

    Both the declared `response_model` and the endpoint's return annotation count.

    Args:
        app: The application to inspect.

    Returns:
        `GET <path>` keys, path without the API prefix.
    """
    found: set[str] = set()
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if not isinstance(route, APIRoute) or "GET" not in (route.methods or set()):
            continue
        returned = get_type_hints(route.endpoint).get("return")
        if is_bare_collection(route.response_model) or is_bare_collection(returned):
            found.add(f"GET {(context.path or '').removeprefix(API_PREFIX)}")
    return found


def test_no_new_endpoint_returns_a_bare_list() -> None:
    new = sorted(bare_list_routes(create_app()) - LEGACY_UNPAGED)
    assert new == [], "Return Page[T] (shared.pagination), see AGENTS.md 'Lists'"


def test_legacy_list_only_shrinks() -> None:
    stale = sorted(LEGACY_UNPAGED - bare_list_routes(create_app()))
    assert stale == [], "Paginated or removed: delete these from LEGACY_UNPAGED"


def violations(**route_kwargs: object) -> set[str]:
    return bare_list_routes(_app_with(**route_kwargs))


def _app_with(**route_kwargs: object) -> FastAPI:
    router = APIRouter()

    @router.get("/things", **route_kwargs)  # ty: ignore[invalid-argument-type]
    async def list_things() -> list[str]:
        return []

    app = FastAPI()
    app.include_router(router)
    return app


def test_a_deliberate_violation_is_caught() -> None:
    assert violations() == {"GET /things"}


def test_response_model_list_is_caught_even_if_the_annotation_is_not() -> None:
    router = APIRouter()

    @router.get("/things", response_model=list[int])
    async def list_things() -> dict[str, int]:
        return {}

    app = FastAPI()
    app.include_router(router)
    assert bare_list_routes(app) == {"GET /things"}


def test_return_annotation_is_caught_without_a_response_model() -> None:
    assert violations(response_model=None) == {"GET /things"}


@pytest.mark.parametrize(
    "annotation",
    [list[str] | None, Annotated[list[str], "x"], Sequence[str], set[str]],
    ids=["optional", "annotated", "sequence", "set"],
)
def test_wrapped_collections_are_caught(annotation: object) -> None:
    assert is_bare_collection(annotation)


def test_pages_and_scalars_are_fine() -> None:
    assert not is_bare_collection(Page[int])
    assert not is_bare_collection(dict[str, int])
    assert not is_bare_collection(str)
    assert not is_bare_collection(None)
