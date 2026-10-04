"""Check-in queries on PostgreSQL."""

from uuid import UUID

from sqlalchemy import Select, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.shared.db.pagination import ordering, paginate
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.checkins.models import TripCheckin
from tuttitrip.trips.checkins.schemas import (
    CheckinFilters,
    CheckinQuery,
    CheckinSort,
    CheckinUpdate,
)

_SORT = {
    CheckinSort.UPDATED_AT: TripCheckin.updated_at,
    CheckinSort.ACCOMMODATION: func.lower(TripCheckin.accommodation),
    CheckinSort.ROOM: TripCheckin.room,
}


def _scoped(trip_id: UUID) -> Select[TripCheckin]:
    return select(TripCheckin).where(TripCheckin.trip_id == trip_id)


def _apply_filters(
    stmt: Select[TripCheckin], filters: CheckinFilters
) -> Select[TripCheckin]:
    if filters.accommodation is not None:
        stmt = stmt.where(
            TripCheckin.accommodation.icontains(filters.accommodation, autoescape=True)
        )
    return stmt


async def select_checkins(
    session: AsyncSession, trip_id: UUID, query: CheckinQuery
) -> Page[TripCheckin]:
    """One page of a trip's check-ins.

    Args:
        session: Open session.
        trip_id: Trip id.
        query: Page, sort, direction and filters.

    Returns:
        The page of rows.
    """
    order = ordering(_SORT, query.sort, TripCheckin.profile_id)
    return await paginate(
        session, _apply_filters(_scoped(trip_id), query), query, order
    )


async def upsert_checkin(
    session: AsyncSession, trip_id: UUID, profile_id: UUID, data: CheckinUpdate
) -> TripCheckin:
    """Insert or replace a profile's check-in and flush.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        profile_id: Profile the entry belongs to.
        data: The new values.

    Returns:
        The stored row.
    """
    values = {"accommodation": data.accommodation, "room": data.room}
    stmt = (
        insert(TripCheckin)
        .values(trip_id=trip_id, profile_id=profile_id, **values)
        .on_conflict_do_update(
            index_elements=[TripCheckin.trip_id, TripCheckin.profile_id],
            set_={**values, "updated_at": func.now()},
        )
        .returning(TripCheckin)
    )
    row = (
        await session.scalars(stmt, execution_options={"populate_existing": True})
    ).one()
    await session.flush()
    return row


async def delete_checkin(
    session: AsyncSession, trip_id: UUID, profile_id: UUID
) -> bool:
    """Delete a profile's check-in.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        profile_id: Profile the entry belongs to.

    Returns:
        Whether a row existed.
    """
    result = await session.execute(
        delete(TripCheckin)
        .where(TripCheckin.trip_id == trip_id, TripCheckin.profile_id == profile_id)
        .returning(TripCheckin.profile_id)
    )
    return result.first() is not None


async def delete_trip_checkins(session: AsyncSession, trip_id: UUID) -> None:
    """Delete every check-in of a trip.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
    """
    await session.execute(delete(TripCheckin).where(TripCheckin.trip_id == trip_id))
