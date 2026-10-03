"""Trip queries on PostgreSQL."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.trips.models import Trip, TripMember
from tuttitrip.trips.schemas import TripRole


async def insert_trip(
    session: AsyncSession, *, owner_sub: str, name: str, destination: str | None
) -> Trip:
    """Insert a trip with its owner as host, and flush to get server defaults.

    Args:
        session: Open session (caller commits).
        owner_sub: Auth0 subject of the organizer.
        name: Trip name.
        destination: Optional destination.

    Returns:
        The persisted trip.
    """
    trip = Trip(owner_sub=owner_sub, name=name, destination=destination)
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


async def select_trips_of_member(
    session: AsyncSession, sub: str
) -> Sequence[tuple[Trip, TripRole]]:
    """Trips the user belongs to, newest first, with the user's role.

    Args:
        session: Open session.
        sub: Auth0 subject.

    Returns:
        ``(trip, role)`` pairs.
    """
    result = await session.execute(
        select(Trip, TripMember.role)
        .join(TripMember, TripMember.trip_id == Trip.id)
        .where(TripMember.user_sub == sub)
        .order_by(Trip.created_at.desc())
    )
    return [(trip, role) for trip, role in result.tuples().all()]


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
