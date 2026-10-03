"""Create and list trips, and check a user's role on a trip (object level)."""

from itertools import starmap
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.trips import db
from tuttitrip.trips.models import Trip
from tuttitrip.trips.schemas import TripCreate, TripMembership, TripRead, TripRole


class TripNotFoundError(Exception):
    """The trip does not exist or the user is not on it (both look the same)."""


class TripRoleError(Exception):
    """The user is on the trip but their role is too low."""


def _read(trip: Trip, role: TripRole) -> TripRead:
    return TripRead(
        id=trip.id,
        name=trip.name,
        destination=trip.destination,
        created_at=trip.created_at,
        my_role=role,
    )


async def create_trip(
    session: AsyncSession, owner_sub: str, data: TripCreate
) -> TripRead:
    """Create a trip with the caller as host and commit.

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
    return _read(trip, TripRole.HOST)


async def list_trips(session: AsyncSession, sub: str) -> list[TripRead]:
    """List the trips the user belongs to.

    Args:
        session: Open session.
        sub: Auth0 subject.

    Returns:
        Trips, newest first, with the user's role.
    """
    return list(starmap(_read, await db.select_trips_of_member(session, sub)))


async def get_membership(
    session: AsyncSession, trip_id: UUID, sub: str, min_role: TripRole
) -> TripMembership:
    """Check that the user has at least ``min_role`` on the trip.

    Other domains call this (or use ``TripAccess`` in their api.py) to
    authorize trip-scoped operations.

    Args:
        session: Open session.
        trip_id: Trip id.
        sub: Auth0 subject of the caller.
        min_role: Minimum trip role.

    Returns:
        The membership.
    """
    role = await db.select_member_role(session, trip_id, sub)
    if role is None:
        raise TripNotFoundError(str(trip_id))
    if not role.satisfies(min_role):
        msg = f"Trip role '{min_role}' required (you are '{role}')"
        raise TripRoleError(msg)
    return TripMembership(trip_id=trip_id, sub=sub, role=role)
