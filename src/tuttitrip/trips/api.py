"""Trip endpoints."""

from fastapi import APIRouter, status

from tuttitrip.shared.auth.api import CurrentUser
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.trips.schemas import TripCreate, TripRead
from tuttitrip.trips.services import trip_service

router = APIRouter(prefix="/trips", tags=["trips"])


@router.get("")
async def list_trips(user: CurrentUser, session: SessionDep) -> list[TripRead]:
    """List the caller's trips.

    Args:
        user: The authenticated organizer.
        session: Database session.

    Returns:
        Trips, newest first.
    """
    return await trip_service.list_trips(session, user.sub)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_trip(
    data: TripCreate, user: CurrentUser, session: SessionDep
) -> TripRead:
    """Create a trip owned by the caller.

    Args:
        data: Trip payload.
        user: The authenticated organizer.
        session: Database session.

    Returns:
        The created trip.
    """
    return await trip_service.create_trip(session, user.sub, data)
