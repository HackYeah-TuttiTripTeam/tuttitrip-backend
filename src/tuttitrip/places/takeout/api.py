"""Takeout import endpoint (nested under a trip)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from tuttitrip.places.takeout.logic.takeout_csv import MAX_BYTES, TakeoutFormatError
from tuttitrip.places.takeout.schemas import TakeoutImportRead
from tuttitrip.places.takeout.services import takeout_service
from tuttitrip.places.takeout.services.takeout_service import TakeoutTripError
from tuttitrip.profiles.feedback.services.feedback_service import (
    ProfileNotFoundError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripCoHost

router = APIRouter(prefix="/trips/{trip_id}/places", tags=["places"])


@router.post(
    "/import",
    summary="Import a Google Maps list (Takeout CSV)",
    description=(
        "Multipart form: `file` is the CSV of a saved list from Google Takeout "
        "(`Saved/<list>.csv`, at most 500 places and 1 MB), `profile_id` is the person "
        "whose list it is. Titles are matched to the catalog of the trip's city by "
        "name; matched places become `want` for that person (a vote they already "
        "cast stays). Unmatched places come back with a reason. No Google Places "
        "data is used."
    ),
    responses={
        404: {"description": "Trip or profile not found, or the caller is not on it."},
        422: {"description": "Not a Takeout list, too big, or the trip has no city."},
    },
    dependencies=[requires(Feature.PROFILES_FEEDBACK, Access.WRITE)],
)
async def import_takeout(
    membership: TripCoHost,
    session: SessionDep,
    file: Annotated[UploadFile, File(description="The Takeout CSV.")],
    profile_id: Annotated[UUID, Form(description="Whose list it is.")],
) -> TakeoutImportRead:
    """Import a list of saved places.

    Args:
        membership: The caller's membership of ``{trip_id}`` (co-host or host).
        session: Database session.
        file: The uploaded CSV.
        profile_id: The person the likes are recorded for.

    Returns:
        Matched and unmatched places.

    Raises:
        HTTPException: 404 for an unknown profile, 422 for a bad file or a trip
            without a city.
    """
    data = await file.read(MAX_BYTES + 1)
    try:
        return await takeout_service.import_takeout(
            session, membership, profile_id, data
        )
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found") from exc
    except (TakeoutFormatError, TakeoutTripError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
