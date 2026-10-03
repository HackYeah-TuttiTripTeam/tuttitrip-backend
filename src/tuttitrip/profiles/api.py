"""Profile endpoints (nested under a trip)."""

from fastapi import APIRouter

from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripMember

router = APIRouter(prefix="/trips/{trip_id}/profiles", tags=["profiles"])


@router.get("", dependencies=[requires(Feature.PROFILES_CORE, Access.READ)])
async def list_profiles(
    membership: TripMember, session: SessionDep
) -> list[ProfileRead]:
    """List the people on a trip the caller belongs to.

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The trip's profiles.
    """
    return await profile_service.list_profiles(session, membership)
