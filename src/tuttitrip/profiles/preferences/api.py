"""Preference endpoints (nested under a trip)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from tuttitrip.profiles.preferences.schemas import PreferencesRead, PreferencesWrite
from tuttitrip.profiles.preferences.services import preference_service
from tuttitrip.profiles.preferences.services.preference_service import (
    ExamplePlaceNotFoundError,
)
from tuttitrip.profiles.services.profile_service import (
    ProfileForbiddenError,
    ProfileNotFoundError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripMember

router = APIRouter(prefix="/trips/{trip_id}", tags=["preferences"])

PROFILE_NOT_FOUND = "Profile not found"


@router.get(
    "/preferences", dependencies=[requires(Feature.PROFILES_PREFERENCES, Access.READ)]
)
async def list_preferences(
    membership: TripMember, session: SessionDep
) -> list[PreferencesRead]:
    """Preferences of everyone on the trip ("what we already know").

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        One entry per person; unfilled ones carry the age defaults.
    """
    return await preference_service.list_preferences(session, membership)


@router.get(
    "/profiles/{profile_id}/preferences",
    dependencies=[requires(Feature.PROFILES_PREFERENCES, Access.READ)],
)
async def get_preferences(
    profile_id: UUID, membership: TripMember, session: SessionDep
) -> PreferencesRead:
    """Preferences of one person.

    Args:
        profile_id: Whose preferences.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The saved preferences, or the age defaults.
    """
    try:
        return await preference_service.get_preferences(session, membership, profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc


@router.put(
    "/profiles/{profile_id}/preferences",
    dependencies=[requires(Feature.PROFILES_PREFERENCES, Access.WRITE)],
)
async def put_preferences(
    profile_id: UUID,
    data: PreferencesWrite,
    membership: TripMember,
    session: SessionDep,
) -> PreferencesRead:
    """Replace your own preferences, or anyone's as a co-host or higher.

    Args:
        profile_id: Whose preferences.
        data: The whole preferences; the pool must add up to 10.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The saved preferences.
    """
    try:
        return await preference_service.replace_preferences(
            session, membership, profile_id, data
        )
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc
    except ProfileForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ExamplePlaceNotFoundError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
