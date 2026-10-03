"""Place catalog queries on PostgreSQL."""

from collections.abc import Collection, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tuttitrip.places.models import City, Place


async def select_cities(session: AsyncSession) -> Sequence[City]:
    """List the covered cities.

    Args:
        session: Open session.

    Returns:
        Cities ordered by name.
    """
    return (await session.scalars(select(City).order_by(City.name))).all()


async def select_places(
    session: AsyncSession,
    city_slug: str,
    category: str | None,
    *,
    limit: int,
    offset: int,
) -> Sequence[Place]:
    """List one page of the places of one city.

    Args:
        session: Open session.
        city_slug: City slug.
        category: Restrict to one category, or None for all.
        limit: Page size.
        offset: Rows to skip.

    Returns:
        Places with their prices, ordered by name (then id, for stable pages).
    """
    query = (
        select(Place)
        .where(Place.city_slug == city_slug)
        .options(selectinload(Place.prices))
        .order_by(Place.name, Place.id)
        .limit(limit)
        .offset(offset)
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


async def select_places_by_ids(
    session: AsyncSession, place_ids: Collection[UUID]
) -> Sequence[Place]:
    """Fetch several places with one query.

    Args:
        session: Open session.
        place_ids: Place ids.

    Returns:
        The places that exist (unknown ids are simply missing).
    """
    if not place_ids:
        return ()
    query = (
        select(Place).where(Place.id.in_(place_ids)).options(selectinload(Place.prices))
    )
    return (await session.scalars(query)).all()
