"""Accommodation endpoints (nested under a trip)."""

from typing import Any

from fastapi import APIRouter, HTTPException, status

from tuttitrip.accommodation.schemas import RequirementsRead, RequirementsWrite
from tuttitrip.accommodation.services import requirements_service
from tuttitrip.accommodation.services.requirements_service import (
    RequirementsInvalidError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripCoHost, TripMember

router = APIRouter(prefix="/trips/{trip_id}/accommodation", tags=["accommodation"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "Trip not found, or the caller is not on it."}
}


@router.get(
    "/requirements",
    summary="Lodging requirements of the trip",
    responses=NOT_FOUND,
    dependencies=[requires(Feature.ACCOMMODATION, Access.READ)],
)
async def get_requirements(
    membership: TripMember, session: SessionDep
) -> RequirementsRead:
    """Read the lodging requirements (any member).

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The requirements and their version.
    """
    return await requirements_service.get_requirements(session, membership.trip_id)


@router.put(
    "/requirements",
    summary="Replace the lodging requirements of the trip",
    description=(
        "Replaces the whole set. Each requirement is an amenity, a platform or "
        "a maximum distance, hard or soft, for all nights (empty `nights`) or "
        "chosen ones. `version` moves only when the set really changes. An "
        "outing (a single day) has no nights and gets 422."
    ),
    responses=NOT_FOUND,
    dependencies=[requires(Feature.ACCOMMODATION, Access.WRITE)],
)
async def put_requirements(
    data: RequirementsWrite, membership: TripCoHost, session: SessionDep
) -> RequirementsRead:
    """Replace the lodging requirements (co-host or host).

    Args:
        data: The whole new set.
        membership: The caller's membership (co-host or host).
        session: Database session.

    Returns:
        The stored requirements and the new version.
    """
    try:
        return await requirements_service.replace_requirements(
            session, membership, data
        )
    except RequirementsInvalidError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
