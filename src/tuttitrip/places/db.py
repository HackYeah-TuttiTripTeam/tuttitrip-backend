"""Place catalog queries on PostgreSQL."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tuttitrip.places.models import Place


async def select_places(
    session: AsyncSession, city_slug: str, category: str | None
) -> Sequence[Place]:
    """List the places of one city.

    Args:
        session: Open session.
        city_slug: City slug.
        category: Restrict to one category, or None for all.

    Returns:
        Places with their prices, ordered by name.
    """
    query = (
        select(Place)
        .where(Place.city_slug == city_slug)
        .options(selectinload(Place.prices))
        .order_by(Place.name, Place.id)
    )
    if category is not None:
        query = query.where(Place.category == category)
    return (await session.scalars(query)).all()


async def select_place(session: AsyncSession, place_id: UUID) -> Place | None:
    """Fetch one place with its prices.

    Args:
        session: Open session.
        place_id: Place id.

    Returns:
        The place, or None.
    """
    return await session.scalar(
        select(Place).where(Place.id == place_id).options(selectinload(Place.prices))
    )
