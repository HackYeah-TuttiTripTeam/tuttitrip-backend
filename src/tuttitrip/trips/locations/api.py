"""Location endpoints: opt-in sharing of members' last position.

Every response is ``Cache-Control: no-store``. See ``location_service`` for
what is stored and for how long.
"""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import no_store, requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripMember
from tuttitrip.trips.locations.schemas import (
    ConsentRead,
    ConsentUpdate,
    LocationQuery,
    LocationRead,
    PositionUpdate,
)
from tuttitrip.trips.locations.services import location_service
from tuttitrip.trips.locations.services.location_service import (
    LocationProfileMissingError,
    SharingOffError,
)

router = APIRouter(prefix="/trips/{trip_id}/locations", tags=["locations"])

NO_PROFILE = "You have no profile on this trip"
SHARING_OFF = "Location sharing is off; turn it on first"
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "Not a member of the trip (or no profile on it)."}
}


@router.get(
    "",
    summary="Positions members currently share",
    responses=NOT_FOUND,
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.READ), no_store()],
)
async def list_locations(
    membership: TripMember,
    session: SessionDep,
    query: Annotated[LocationQuery, Query()],
) -> Page[LocationRead]:
    """List the last positions of members who share, valid ones only.

    A position is missing when the person never shared, stopped, the consent
    lapsed or the position expired (default 15 minutes after the last update).

    Args:
        membership: The caller's membership of `{trip_id}`.
        session: Database session.
        query: Page, sort and filters.

    Returns:
        One page of positions, newest first by default.
    """
    return await location_service.list_locations(session, membership, query)


@router.get(
    "/me/consent",
    responses=NOT_FOUND,
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.READ), no_store()],
)
async def get_my_consent(membership: TripMember, session: SessionDep) -> ConsentRead:
    """Tell whether the caller shares their location on this trip (default: no).

    Args:
        membership: The caller's membership of `{trip_id}`.
        session: Database session.

    Returns:
        The state and when it lapses.
    """
    try:
        return await location_service.get_consent(session, membership)
    except LocationProfileMissingError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_PROFILE) from exc


@router.put(
    "/me/consent",
    responses=NOT_FOUND,
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.WRITE), no_store()],
)
async def set_my_consent(
    data: ConsentUpdate, membership: TripMember, session: SessionDep
) -> ConsentRead:
    """Start sharing for 5 minutes to 24 hours (default 4 hours); repeat to extend.

    Args:
        data: How long to share.
        membership: The caller's membership of `{trip_id}`.
        session: Database session.

    Returns:
        The new state.
    """
    try:
        return await location_service.set_consent(session, membership, data)
    except LocationProfileMissingError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_PROFILE) from exc


@router.put(
    "/me",
    responses={
        **NOT_FOUND,
        403: {"description": "Sharing is off: the position is not stored."},
    },
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.WRITE), no_store()],
)
async def update_my_position(
    data: PositionUpdate, membership: TripMember, session: SessionDep
) -> LocationRead:
    """Send the caller's current position (every few minutes while sharing).

    Only the latest position is kept; it expires after 15 minutes unless
    updated. Without a live consent the position is refused (403) and not stored.

    Args:
        data: The position.
        membership: The caller's membership of `{trip_id}`.
        session: Database session.

    Returns:
        The stored position.
    """
    try:
        return await location_service.update_position(session, membership, data)
    except LocationProfileMissingError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_PROFILE) from exc
    except SharingOffError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, SHARING_OFF) from exc


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.WRITE), no_store()],
)
async def stop_sharing(membership: TripMember, session: SessionDep) -> None:
    """Stop sharing now: the consent and the last position are deleted.

    Args:
        membership: The caller's membership of `{trip_id}`.
        session: Database session.
    """
    try:
        await location_service.stop_sharing(session, membership)
    except LocationProfileMissingError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_PROFILE) from exc
