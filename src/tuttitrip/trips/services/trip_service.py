"""Create, list and authorize access to trips."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.trips import db
from tuttitrip.trips.schemas import TripCreate, TripRead


class TripNotFoundError(Exception):
    """The trip does not exist or belongs to another organizer."""


async def create_trip(
    session: AsyncSession, owner_sub: str, data: TripCreate
) -> TripRead:
    """Create a trip for the organizer and commit.

    Args:
        session: Open session.
        owner_sub: Auth0 subject of the organizer.
        data: Validated payload.

    Returns:
        The created trip.
    """
    trip = await db.insert_trip(
        session, owner_sub=owner_sub, name=data.name, destination=data.destination
    )
    await session.commit()
    return TripRead.model_validate(trip)


async def list_trips(session: AsyncSession, owner_sub: str) -> list[TripRead]:
    """List the organizer's trips.

    Args:
        session: Open session.
        owner_sub: Auth0 subject of the organizer.

    Returns:
        Trips, newest first.
    """
    trips = await db.select_trips_by_owner(session, owner_sub)
    return [TripRead.model_validate(trip) for trip in trips]


async def get_owned_trip(
    session: AsyncSession, trip_id: UUID, owner_sub: str
) -> TripRead:
    """Return a trip only if the caller owns it.

    Other domains call this to authorize trip-scoped operations.

    Args:
        session: Open session.
        trip_id: Trip id.
        owner_sub: Auth0 subject of the caller.

    Returns:
        The trip.
    """
    trip = await db.select_owned_trip(session, trip_id, owner_sub)
    if trip is None:
        raise TripNotFoundError(str(trip_id))
    return TripRead.model_validate(trip)
