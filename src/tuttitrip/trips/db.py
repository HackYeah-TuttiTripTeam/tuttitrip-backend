"""Trip queries on PostgreSQL."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.trips.models import Trip


async def insert_trip(
    session: AsyncSession, *, owner_sub: str, name: str, destination: str | None
) -> Trip:
    """Insert a trip and flush it to get server defaults.

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
    await session.refresh(trip)
    return trip


async def select_trips_by_owner(
    session: AsyncSession, owner_sub: str
) -> Sequence[Trip]:
    """List an organizer's trips, newest first.

    Args:
        session: Open session.
        owner_sub: Auth0 subject of the organizer.

    Returns:
        The organizer's trips.
    """
    result = await session.scalars(
        select(Trip).where(Trip.owner_sub == owner_sub).order_by(Trip.created_at.desc())
    )
    return result.all()


async def select_owned_trip(
    session: AsyncSession, trip_id: UUID, owner_sub: str
) -> Trip | None:
    """Fetch one trip if it belongs to the organizer.

    Args:
        session: Open session.
        trip_id: Trip id.
        owner_sub: Auth0 subject of the organizer.

    Returns:
        The trip, or None if missing or owned by someone else.
    """
    return await session.scalar(
        select(Trip).where(Trip.id == trip_id, Trip.owner_sub == owner_sub)
    )
