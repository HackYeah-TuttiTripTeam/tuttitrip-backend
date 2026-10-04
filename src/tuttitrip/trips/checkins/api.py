"""Check-in endpoints: members tell the group where they stay."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripMember
from tuttitrip.trips.checkins.schemas import CheckinQuery, CheckinRead, CheckinUpdate
from tuttitrip.trips.checkins.services import checkin_service
from tuttitrip.trips.checkins.services.checkin_service import (
    CheckinForbiddenError,
    CheckinProfileNotFoundError,
    CheckinTripOverError,
)

router = APIRouter(prefix="/trips/{trip_id}/checkins", tags=["checkins"])

PROFILE_NOT_FOUND = "Profile not found"
TRIP_OVER = "The trip is over"
EDIT_ERRORS: dict[int | str, dict[str, str]] = {
    403: {
        "description": (
            "Not the caller's own entry; the host may also change entries of "
            "profiles without an account."
        )
    },
    404: {"description": "Not a member of the trip, or the profile is not on it."},
}


@router.get(
    "",
    summary="Where everyone stays",
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.READ)],
)
async def list_checkins(
    membership: TripMember,
    session: SessionDep,
    query: Annotated[CheckinQuery, Query()],
) -> Page[CheckinRead]:
    """List the check-ins of the trip's members (any member sees them all).

    Entries are deleted once the trip has ended.

    Args:
        membership: The caller's membership of `{trip_id}`.
        session: Database session.
        query: Page, sort and filters.

    Returns:
        One page of entries, by accommodation by default.
    """
    return await checkin_service.list_checkins(session, membership, query)


@router.put(
    "/{profile_id}",
    responses={**EDIT_ERRORS, 409: {"description": TRIP_OVER}},
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.WRITE)],
)
async def set_checkin(
    profile_id: UUID,
    data: CheckinUpdate,
    membership: TripMember,
    session: SessionDep,
) -> CheckinRead:
    """Set or replace where a profile stays and its room number.

    A member sets their own profile; the host also those without an account.

    Args:
        profile_id: Profile the entry is for (`profile_id` from the members list).
        data: Accommodation and room.
        membership: The caller's membership of `{trip_id}`.
        session: Database session.

    Returns:
        The stored entry.
    """
    try:
        return await checkin_service.set_checkin(session, membership, profile_id, data)
    except CheckinProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc
    except CheckinForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except CheckinTripOverError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, TRIP_OVER) from exc


@router.delete(
    "/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=EDIT_ERRORS,
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.WRITE)],
)
async def delete_checkin(
    profile_id: UUID, membership: TripMember, session: SessionDep
) -> None:
    """Remove a profile's entry (same rules as setting it; idempotent).

    Args:
        profile_id: Profile whose entry goes.
        membership: The caller's membership of `{trip_id}`.
        session: Database session.
    """
    try:
        await checkin_service.clear_checkin(session, membership, profile_id)
    except CheckinProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc
    except CheckinForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
