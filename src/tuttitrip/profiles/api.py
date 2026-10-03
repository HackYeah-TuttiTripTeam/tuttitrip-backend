"""Profile endpoints (nested under a trip)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from tuttitrip.profiles.logic.weight_presets import (
    FocusProfileRequiredError,
    WeightRatioError,
)
from tuttitrip.profiles.schemas import (
    ProfileCreate,
    ProfileRead,
    ProfileUpdate,
    WeightsUpdate,
)
from tuttitrip.profiles.services import profile_service
from tuttitrip.profiles.services.profile_service import (
    ProfileAccountError,
    ProfileForbiddenError,
    ProfileNotFoundError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripCoHost, TripMember

router = APIRouter(prefix="/trips/{trip_id}/profiles", tags=["profiles"])

PROFILE_NOT_FOUND = "Profile not found"


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


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[requires(Feature.PROFILES_CORE, Access.WRITE)],
)
async def create_profile(
    data: ProfileCreate, membership: TripCoHost, session: SessionDep
) -> ProfileRead:
    """Add a person; comfort fields not given come from their age.

    Args:
        data: Name, age, optional account link and comfort overrides.
        membership: The caller's co-host (or higher) membership.
        session: Database session.

    Returns:
        The created profile.
    """
    try:
        return await profile_service.create_profile(session, membership, data)
    except ProfileAccountError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.put("/weights", dependencies=[requires(Feature.PROFILES_CORE, Access.WRITE)])
async def set_weights(
    data: WeightsUpdate, membership: TripCoHost, session: SessionDep
) -> list[ProfileRead]:
    """Set weights by a preset or a list (``max/min`` must stay at most 3).

    Args:
        data: A preset or explicit weights.
        membership: The caller's co-host (or higher) membership.
        session: Database session.

    Returns:
        All profiles of the trip with the new weights.
    """
    try:
        return await profile_service.set_weights(session, membership, data)
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc
    except (WeightRatioError, FocusProfileRequiredError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.patch(
    "/{profile_id}", dependencies=[requires(Feature.PROFILES_CORE, Access.WRITE)]
)
async def update_profile(
    profile_id: UUID,
    data: ProfileUpdate,
    membership: TripMember,
    session: SessionDep,
) -> ProfileRead:
    """Edit your own profile, or any profile as a co-host or higher.

    Args:
        profile_id: Profile to edit.
        data: Fields to change.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The updated profile.
    """
    try:
        return await profile_service.update_profile(
            session, membership, profile_id, data
        )
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc
    except ProfileForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ProfileAccountError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.delete(
    "/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[requires(Feature.PROFILES_CORE, Access.WRITE)],
)
async def delete_profile(
    profile_id: UUID, membership: TripCoHost, session: SessionDep
) -> Response:
    """Remove a person from the trip.

    Args:
        profile_id: Profile to delete.
        membership: The caller's co-host (or higher) membership.
        session: Database session.

    Returns:
        An empty 204 response.
    """
    try:
        await profile_service.delete_profile(session, membership, profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
