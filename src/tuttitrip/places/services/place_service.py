"""Read the place catalog."""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.places import db
from tuttitrip.places.cities.logic.resolve import city_slug_for
from tuttitrip.places.schemas import CityRead, PlaceCategory, PlaceRead, TransitFareRead


class PlaceNotFoundError(Exception):
    """The place does not exist."""


ALL_PLACES_PAGE = 200


async def list_cities(session: AsyncSession) -> list[CityRead]:
    """List the covered cities.

    Args:
        session: Open session.

    Returns:
        Cities ordered by name.
    """
    cities = await db.select_cities(session)
    return [CityRead.model_validate(city) for city in cities]


async def find_city_slug(session: AsyncSession, destination: str) -> str | None:
    """The city slug for a free-text destination.

    Args:
        session: Open session.
        destination: E.g. ``"Kraków"``; case and diacritics do not matter.

    Returns:
        The catalog city's slug or ``slugify(destination)``; None for no letters.
    """
    cities = await db.select_cities(session)
    return city_slug_for(destination, ((c.slug, c.name) for c in cities))


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


async def list_all_places(session: AsyncSession, city_slug: str) -> list[PlaceRead]:
    """Every catalog place of a city, read page by page.

    Args:
        session: Open session.
        city_slug: City slug.

    Returns:
        The places ordered by name; empty for an unknown city.
    """
    found: list[PlaceRead] = []
    while page := await list_places(
        session, city_slug, None, limit=ALL_PLACES_PAGE, offset=len(found)
    ):
        found.extend(page)
        if len(page) < ALL_PLACES_PAGE:
            break
    return found


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


async def get_places(
    session: AsyncSession, place_ids: Collection[UUID]
) -> dict[UUID, PlaceRead]:
    """Fetch several places with one query.

    Args:
        session: Open session.
        place_ids: Place ids.

    Returns:
        The existing places by id; unknown ids are absent.
    """
    places = await db.select_places_by_ids(session, set(place_ids))
    return {place.id: PlaceRead.model_validate(place) for place in places}


async def list_fares(session: AsyncSession, city_slug: str) -> list[TransitFareRead]:
    """Public transport fares of a city.

    Args:
        session: Open session.
        city_slug: City slug.

    Returns:
        The fares; empty for a city without a tariff in the sheet.
    """
    return [
        TransitFareRead.model_validate(fare)
        for fare in await db.select_fares(session, city_slug)
    ]
