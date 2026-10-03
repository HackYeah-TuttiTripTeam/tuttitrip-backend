"""Profile endpoints (nested under a trip)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.api import CurrentUser
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.trips.services.trip_service import TripNotFoundError

router = APIRouter(prefix="/trips/{trip_id}/profiles", tags=["profiles"])


@router.get("")
async def list_profiles(
    trip_id: UUID, user: CurrentUser, session: SessionDep
) -> list[ProfileRead]:
    """List the people on one of the caller's trips.

    Args:
        trip_id: Trip id.
        user: The authenticated organizer.
        session: Database session.

    Returns:
        The trip's profiles.
    """
    try:
        return await profile_service.list_profiles(session, trip_id, user.sub)
    except TripNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trip not found") from exc
