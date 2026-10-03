"""Read the place catalog."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.places import db
from tuttitrip.places.schemas import CityRead, PlaceCategory, PlaceRead


class PlaceNotFoundError(Exception):
    """The place does not exist."""


async def list_cities(session: AsyncSession) -> list[CityRead]:
    """List the covered cities.

    Args:
        session: Open session.

    Returns:
        Cities ordered by name.
    """
    cities = await db.select_cities(session)
    return [CityRead.model_validate(city) for city in cities]


async def list_places(
    session: AsyncSession,
    city_slug: str,
    category: PlaceCategory | None,
    *,
    limit: int,
    offset: int,
) -> list[PlaceRead]:
    """List one page of the catalog places of a city.

    Args:
        session: Open session.
        city_slug: City slug.
        category: Restrict to one category, or None.
        limit: Page size.
        offset: Rows to skip.

    Returns:
        The places ordered by name; empty for an unknown city.
    """
    places = await db.select_places(
        session,
        city_slug,
        category.value if category else None,
        limit=limit,
        offset=offset,
    )
    return [PlaceRead.model_validate(place) for place in places]


async def get_place(session: AsyncSession, place_id: UUID) -> PlaceRead:
    """Fetch one place.

    Args:
        session: Open session.
        place_id: Place id.

    Returns:
        The place.

    Raises:
        PlaceNotFoundError: When there is no such place.
    """
    place = await db.select_place(session, place_id)
    if place is None:
        raise PlaceNotFoundError(str(place_id))
    return PlaceRead.model_validate(place)
