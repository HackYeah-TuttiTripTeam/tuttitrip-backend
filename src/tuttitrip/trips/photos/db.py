"""Photo queries on PostgreSQL."""

from uuid import UUID

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from tuttitrip.shared.db.pagination import ordering, paginate
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.photos.models import TripPhoto
from tuttitrip.trips.photos.schemas import PhotoFilters, PhotoQuery, PhotoSort

_SORT = {
    PhotoSort.CREATED_AT: TripPhoto.created_at,
    PhotoSort.SIZE_BYTES: TripPhoto.size_bytes,
}


def _scoped(trip_id: UUID) -> Select[TripPhoto]:
    return (
        select(TripPhoto)
        .where(TripPhoto.trip_id == trip_id)
        .options(defer(TripPhoto.image))
    )


def _apply_filters(
    stmt: Select[TripPhoto], filters: PhotoFilters, sub: str
) -> Select[TripPhoto]:
    if filters.mine is True:
        stmt = stmt.where(TripPhoto.author_sub == sub)
    elif filters.mine is False:
        stmt = stmt.where(TripPhoto.author_sub != sub)
    return stmt


async def select_photos(
    session: AsyncSession, trip_id: UUID, sub: str, query: PhotoQuery
) -> Page[TripPhoto]:
    """One page of a trip's photos, without the full images.

    Args:
        session: Open session.
        trip_id: Trip id.
        sub: Auth0 subject of the caller (for the ``mine`` filter).
        query: Page, sort, direction and filters.

    Returns:
        The page of rows.
    """
    order = ordering(_SORT, query.sort, TripPhoto.id)
    return await paginate(
        session, _apply_filters(_scoped(trip_id), query, sub), query, order
    )


async def count_photos(session: AsyncSession, trip_id: UUID) -> int:
    """Number of photos on a trip.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The count.
    """
    stmt = (
        select(func.count()).select_from(TripPhoto).where(TripPhoto.trip_id == trip_id)
    )
    return await session.scalar(stmt) or 0


async def insert_photo(session: AsyncSession, photo: TripPhoto) -> TripPhoto:
    """Insert a photo and flush to get server defaults.

    Args:
        session: Open session (caller commits).
        photo: The new row.

    Returns:
        The same row, refreshed.
    """
    session.add(photo)
    await session.flush()
    await session.refresh(photo)
    return photo


async def select_photo(
    session: AsyncSession, trip_id: UUID, photo_id: UUID, *, with_image: bool
) -> TripPhoto | None:
    """One photo of the trip.

    Args:
        session: Open session.
        trip_id: Trip id (the photo must belong to it).
        photo_id: Photo id.
        with_image: Load the full image too.

    Returns:
        The row, or None.
    """
    stmt = select(TripPhoto).where(
        TripPhoto.trip_id == trip_id, TripPhoto.id == photo_id
    )
    if not with_image:
        stmt = stmt.options(defer(TripPhoto.image))
    return await session.scalar(stmt)


async def delete_photo(session: AsyncSession, trip_id: UUID, photo_id: UUID) -> None:
    """Delete a photo.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        photo_id: Photo id.
    """
    await session.execute(
        delete(TripPhoto).where(TripPhoto.trip_id == trip_id, TripPhoto.id == photo_id)
    )
