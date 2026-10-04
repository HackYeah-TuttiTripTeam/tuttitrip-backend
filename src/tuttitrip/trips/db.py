"""Trip queries on PostgreSQL."""

from typing import Any
from uuid import UUID

from sqlalchemy import Select, delete, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.shared.db.pagination import Column, ordering, paginate
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.models import Trip, TripMember
from tuttitrip.trips.schemas import (
    MemberStatus,
    TripFilter,
    TripListQuery,
    TripRole,
    TripSort,
    TripWhen,
)


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
    session.add(
        TripMember(
            trip_id=trip.id,
            user_sub=owner_sub,
            role=TripRole.HOST,
            status=MemberStatus.CONFIRMED,
        )
    )
    await session.flush()
    await session.refresh(trip)
    return trip


async def insert_member(
    session: AsyncSession,
    trip_id: UUID,
    sub: str,
    role: TripRole,
    status: MemberStatus = MemberStatus.PENDING,
) -> None:
    """Add a user to a trip with a role and flush.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        sub: Auth0 subject.
        role: The role to give.
        status: Participation status; a newcomer has to confirm.
    """
    session.add(TripMember(trip_id=trip_id, user_sub=sub, role=role, status=status))
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
    if filters.when is TripWhen.PAST:
        stmt = stmt.where(Trip.end_date < func.current_date())
    elif filters.when is TripWhen.UPCOMING:
        stmt = stmt.where(
            or_(Trip.end_date.is_(None), Trip.end_date >= func.current_date())
        )
    if filters.status:
        stmt = stmt.where(TripMember.status == filters.status)
    return stmt


async def select_trips_page(
    session: AsyncSession, sub: str, query: TripListQuery
) -> tuple[Page[Trip], dict[UUID, tuple[TripRole, MemberStatus]]]:
    """One page of the user's trips, with the user's role and status on each.

    Args:
        session: Open session.
        sub: Auth0 subject.
        query: Paging, sort and filters.

    Returns:
        The page of trips and the user's ``(role, status)`` by trip id.
    """
    page = await paginate(
        session,
        apply_filters(scoped(sub), query),
        query,
        ordering(SORT_COLUMNS, query.sort, Trip.id),
    )
    if not page.items:
        return page, {}
    rows = await session.execute(
        select(TripMember.trip_id, TripMember.role, TripMember.status).where(
            TripMember.user_sub == sub,
            TripMember.trip_id.in_([trip.id for trip in page.items]),
        )
    )
    return page, {trip_id: (role, status) for trip_id, role, status in rows}


async def select_membership(
    session: AsyncSession, trip_id: UUID, sub: str
) -> tuple[TripRole, MemberStatus] | None:
    """The user's role and status on a trip.

    Args:
        session: Open session.
        trip_id: Trip id.
        sub: Auth0 subject.

    Returns:
        ``(role, status)``, or None if the user is not on the trip.
    """
    member = await session.scalar(
        select(TripMember)
        .where(TripMember.trip_id == trip_id, TripMember.user_sub == sub)
        .execution_options(populate_existing=True)
    )
    return None if member is None else (member.role, member.status)


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


async def select_members(
    session: AsyncSession, trip_id: UUID
) -> dict[str, tuple[TripRole, MemberStatus]]:
    """Role and status of all members of a trip.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        ``(role, status)`` by Auth0 subject.
    """
    result = await session.execute(
        select(TripMember.user_sub, TripMember.role, TripMember.status).where(
            TripMember.trip_id == trip_id
        )
    )
    return {row.user_sub: (row.role, row.status) for row in result.all()}


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


async def update_member_status(
    session: AsyncSession, trip_id: UUID, sub: str, status: MemberStatus
) -> None:
    """Set a member's participation status.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        sub: Auth0 subject.
        status: The new status.
    """
    await session.execute(
        update(TripMember)
        .where(TripMember.trip_id == trip_id, TripMember.user_sub == sub)
        .values(status=status)
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


async def select_hosted_trip_ids(session: AsyncSession, sub: str) -> list[UUID]:
    """Trips the user hosts.

    Args:
        session: Open session.
        sub: Auth0 subject.

    Returns:
        Trip ids.
    """
    result = await session.scalars(
        select(TripMember.trip_id).where(
            TripMember.user_sub == sub, TripMember.role == TripRole.HOST
        )
    )
    return list(result.all())


async def select_successor(
    session: AsyncSession, trip_id: UUID, leaving: str
) -> str | None:
    """Who takes over a trip: the longest-standing co-host, else member.

    Args:
        session: Open session.
        trip_id: Trip id.
        leaving: Auth0 subject of the host who leaves (never chosen).

    Returns:
        Auth0 subject, or None when the host is alone on the trip.
    """
    return await session.scalar(
        select(TripMember.user_sub)
        .where(TripMember.trip_id == trip_id, TripMember.user_sub != leaving)
        .order_by(
            (TripMember.role == TripRole.CO_HOST).desc(),
            TripMember.added_at,
            TripMember.user_sub,
        )
        .limit(1)
    )


async def hand_over_trip(session: AsyncSession, trip_id: UUID, new_host: str) -> None:
    """Make an existing member the host and owner (caller commits).

    Args:
        session: Open session.
        trip_id: Trip id.
        new_host: Auth0 subject of the member.
    """
    await session.execute(
        update(Trip).where(Trip.id == trip_id).values(owner_sub=new_host)
    )
    await update_member_role(session, trip_id, new_host, TripRole.HOST)


async def delete_memberships(session: AsyncSession, sub: str) -> int:
    """Remove the user from every trip (caller commits).

    Args:
        session: Open session.
        sub: Auth0 subject.

    Returns:
        How many memberships were removed.
    """
    result = await session.execute(
        delete(TripMember)
        .where(TripMember.user_sub == sub)
        .returning(TripMember.user_sub)
    )
    return len(result.all())
