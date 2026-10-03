"""Place catalog endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tuttitrip.places.schemas import CityRead, PlaceCategory, PlaceRead
from tuttitrip.places.services import place_service
from tuttitrip.places.services.place_service import PlaceNotFoundError
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature

router = APIRouter(prefix="/places", tags=["places"])

MAX_LIMIT = 500


@router.get("/cities", dependencies=[requires(Feature.PLACES_CATALOG, Access.READ)])
async def list_cities(session: SessionDep) -> list[CityRead]:
    """List the cities the planner covers, with time zone and currency.

    Args:
        session: Database session.

    Returns:
        Cities ordered by name.
    """
    return await place_service.list_cities(session)


@router.get("", dependencies=[requires(Feature.PLACES_CATALOG, Access.READ)])
async def list_places(
    session: SessionDep,
    city: Annotated[str, Query(description="City slug, e.g. krakow.")],
    category: Annotated[PlaceCategory | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PlaceRead]:
    """List a page of the catalog places of a city, ordered by name.

    Args:
        session: Database session.
        city: City slug.
        category: Restrict to one category.
        limit: Page size.
        offset: Rows to skip.

    Returns:
        Places with prices and opening hours, each with its verification mark.
    """
    return await place_service.list_places(
        session, city, category, limit=limit, offset=offset
    )


@router.get("/{place_id}", dependencies=[requires(Feature.PLACES_CATALOG, Access.READ)])
async def get_place(place_id: UUID, session: SessionDep) -> PlaceRead:
    """Fetch one catalog place.

    Args:
        place_id: Place id.
        session: Database session.

    Returns:
        The place with prices and opening hours.

    Raises:
        HTTPException: 404 when the place does not exist.
    """
    try:
        return await place_service.get_place(session, place_id)
    except PlaceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Place not found") from exc
