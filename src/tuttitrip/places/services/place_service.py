"""Read the place catalog."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.places import db
from tuttitrip.places.models import Place
from tuttitrip.places.schemas import (
    OpeningHours,
    PlaceCategory,
    PlaceHours,
    PlacePriceRead,
    PlaceRead,
)


class PlaceNotFoundError(Exception):
    """The place does not exist."""


def to_read(place: Place) -> PlaceRead:
    """Map a place row (with loaded prices) to its DTO.

    Args:
        place: The ORM row; ``prices`` must be loaded.

    Returns:
        The DTO. Hours are marked unverified unless a source confirmed them.
    """
    hours = PlaceHours(
        opening_hours=(
            OpeningHours.model_validate(place.opening_hours)
            if place.opening_hours is not None
            else None
        ),
        source_url=place.hours_source_url,
        verified=place.hours_verified,
        checked_at=place.hours_checked_at,
    )
    return PlaceRead.model_validate(
        {
            **{
                name: getattr(place, name)
                for name in PlaceRead.model_fields
                if name not in {"hours", "prices"}
            },
            "hours": hours,
            "prices": [PlacePriceRead.model_validate(p) for p in place.prices],
        }
    )


async def list_places(
    session: AsyncSession, city_slug: str, category: PlaceCategory | None
) -> list[PlaceRead]:
    """List the catalog places of a city.

    Args:
        session: Open session.
        city_slug: City slug.
        category: Restrict to one category, or None.

    Returns:
        The places ordered by name; empty for an unknown city.
    """
    places = await db.select_places(
        session, city_slug, category.value if category else None
    )
    return [to_read(place) for place in places]


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
    return to_read(place)
