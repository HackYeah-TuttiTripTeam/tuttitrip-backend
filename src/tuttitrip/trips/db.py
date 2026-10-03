"""Trip queries on PostgreSQL."""

from typing import Any
from uuid import UUID

from sqlalchemy import Select, delete, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.shared.db.pagination import Column, ordering, paginate
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.models import Trip, TripMember
from tuttitrip.trips.schemas import TripFilter, TripListQuery, TripRole, TripSort


async def insert_trip(
    session: AsyncSession, *, owner_sub: str, fields: dict[str, Any]
) -> Trip:
    """Insert a trip with its owner as host, and flush to get server defaults.

    Args:
        session: Open session (caller commits).
        owner_sub: Auth0 subject of the organizer.
        fields: The trip columns from the payload (``name`` and any detail sent).

    Returns:
        The persisted trip.
    """
    trip = Trip(owner_sub=owner_sub, **fields)
    session.add(trip)
    await session.flush()
    session.add(TripMember(trip_id=trip.id, user_sub=owner_sub, role=TripRole.HOST))
    await session.flush()
    await session.refresh(trip)
    return trip


async def insert_member(
    session: AsyncSession, trip_id: UUID, sub: str, role: TripRole
) -> None:
    """Add a user to a trip with a role and flush.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        sub: Auth0 subject.
        role: The role to give.
    """
    session.add(TripMember(trip_id=trip_id, user_sub=sub, role=role))
    await session.flush()


type TripSelect = Select[Trip]

SORT_COLUMNS: dict[TripSort, Column] = {
    TripSort.CREATED_AT: Trip.created_at,
    TripSort.START_DATE: Trip.start_date,
    TripSort.NAME: func.lower(Trip.name),
}
"""Sort keys of the trip list; the client never names a column."""


def scoped(sub: str) -> TripSelect:
    """The trips the user belongs to (the scope of every trip list and bulk).

    Args:
        sub: Auth0 subject.

    Returns:
        A select of trips, unfiltered and unordered.
    """
    return (
        select(Trip)
        .join(TripMember, TripMember.trip_id == Trip.id)
        .where(TripMember.user_sub == sub)
    )


def apply_filters(stmt: TripSelect, filters: TripFilter) -> TripSelect:
    """Add the list filters to a select scoped by ``scoped``.

    Args:
        stmt: The scoped select.
        filters: Validated filters.

    Returns:
        The narrowed select.
    """
    if filters.q:
        stmt = stmt.where(
            or_(
                Trip.name.icontains(filters.q, autoescape=True),
                Trip.destination.icontains(filters.q, autoescape=True),
            )
        )
    if filters.city:
        stmt = stmt.where(Trip.city_slug == filters.city)
    if filters.kind:
        outing = Trip.start_date == Trip.end_date  # NULL for undated trips
        stmt = stmt.where(
            outing if filters.kind == "outing" else func.coalesce(~outing, true())
        )
    if filters.start_from:
        stmt = stmt.where(Trip.start_date >= filters.start_from)
    if filters.start_to:
        stmt = stmt.where(Trip.start_date <= filters.start_to)
    if filters.role:
        stmt = stmt.where(TripMember.role.in_(filters.role))
    return stmt


async def select_trips_page(
    session: AsyncSession, sub: str, query: TripListQuery
) -> tuple[Page[Trip], dict[UUID, TripRole]]:
    """One page of the user's trips, with the user's role on each.

    Args:
        session: Open session.
        sub: Auth0 subject.
        query: Paging, sort and filters.

    Returns:
        The page of trips and the user's role by trip id.
    """
    page = await paginate(
        session,
        apply_filters(scoped(sub), query),
        query,
        ordering(SORT_COLUMNS, query.sort, Trip.id),
    )
    rows = await session.execute(
        select(TripMember.trip_id, TripMember.role).where(
            TripMember.user_sub == sub,
            TripMember.trip_id.in_([trip.id for trip in page.items]),
        )
    )
    return page, dict(rows.all())


async def select_member_role(
    session: AsyncSession, trip_id: UUID, sub: str
) -> TripRole | None:
    """The user's role on a trip.

    Args:
        session: Open session.
        trip_id: Trip id.
        sub: Auth0 subject.

    Returns:
        The role, or None if the trip is missing or the user is not on it.
    """
    return await session.scalar(
        select(TripMember.role).where(
            TripMember.trip_id == trip_id, TripMember.user_sub == sub
        )
    )


async def select_trip(session: AsyncSession, trip_id: UUID) -> Trip | None:
    """Load a trip.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The trip, or None.
    """
    return await session.get(Trip, trip_id)


async def delete_trip(session: AsyncSession, trip_id: UUID) -> None:
    """Delete a trip in SQL so the database cascades to everything under it.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
    """
    await session.execute(delete(Trip).where(Trip.id == trip_id))


async def select_member_roles(
    session: AsyncSession, trip_id: UUID
) -> dict[str, TripRole]:
    """Roles of all members of a trip.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        Role by Auth0 subject.
    """
    result = await session.execute(
        select(TripMember.user_sub, TripMember.role).where(
            TripMember.trip_id == trip_id
        )
    )
    return dict(result.tuples().all())


async def delete_member(session: AsyncSession, trip_id: UUID, sub: str) -> None:
    """Remove a user from a trip.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        sub: Auth0 subject.
    """
    await session.execute(
        delete(TripMember).where(
            TripMember.trip_id == trip_id, TripMember.user_sub == sub
        )
    )


async def update_member_role(
    session: AsyncSession, trip_id: UUID, sub: str, role: TripRole
) -> None:
    """Set a member's role.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        sub: Auth0 subject.
        role: The new role.
    """
    await session.execute(
        update(TripMember)
        .where(TripMember.trip_id == trip_id, TripMember.user_sub == sub)
        .values(role=role)
    )


async def delete_trips_owned_by(session: AsyncSession, owner_sub: str) -> int:
    """Delete every trip created by one user (the database cascades).

    Args:
        session: Open session (caller commits).
        owner_sub: Auth0 subject of the creator.

    Returns:
        How many trips were deleted.
    """
    result = await session.execute(
        delete(Trip).where(Trip.owner_sub == owner_sub).returning(Trip.id)
    )
    return len(result.all())
