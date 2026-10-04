"""Google exports of an approved plan (nested under a trip)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from tuttitrip.planning.exports.schemas import CalendarExportRead, DriveExportRead
from tuttitrip.planning.exports.services import export_service
from tuttitrip.planning.plans.schemas import (
    NotApprovedDetail,
    NotApprovedError,
)
from tuttitrip.planning.plans.services.plan_service import (
    PlanNotApprovedError,
    PlanNotFoundError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.google.api import GoogleDep
from tuttitrip.shared.google.constants import (
    SCOPE_CALENDAR_APP_CREATED,
    SCOPE_DRIVE_FILE,
)
from tuttitrip.shared.google.schemas import (
    GoogleAccessError,
    GoogleErrorBody,
    GoogleErrorCode,
    GoogleErrorDetail,
)
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripMember

router = APIRouter(prefix="/trips/{trip_id}/plans/{plan_id}/google", tags=["planning"])

_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"description": "Plan, trip not found or caller not on it."},
    409: {
        "model": GoogleErrorBody,
        "description": (
            "The plan is not approved (`plan.not_approved`), or Google has to be "
            "connected first (`google.not_connected`, `google.scope_missing`, "
            "`google.token_expired`): sign in with Google again asking for "
            "`required_scope`. `fallback_url` is the `.ics` file of the plan."
        ),
    },
    502: {
        "description": "Google or Auth0 could not be reached (`google.unavailable`)."
    },
}
_CONSENT_CODES = {
    GoogleErrorCode.NOT_CONNECTED,
    GoogleErrorCode.SCOPE_MISSING,
    GoogleErrorCode.TOKEN_EXPIRED,
}


def _error(
    request: Request, trip_id: UUID, plan_id: UUID, scope: str, error: GoogleAccessError
) -> JSONResponse:
    if error.code not in _CONSENT_CODES:
        body = {"detail": {"code": error.code.value, "message": error.message}}
        return JSONResponse(body, status.HTTP_502_BAD_GATEWAY)
    fallback = request.url_for("get_plan_calendar", trip_id=trip_id, plan_id=plan_id)
    detail = GoogleErrorDetail.model_validate(
        {
            "code": error.code,
            "message": error.message,
            "required_scope": scope,
            "fallback_url": fallback.path,
        }
    )
    return JSONResponse(
        GoogleErrorBody(detail=detail).model_dump(mode="json"),
        status.HTTP_409_CONFLICT,
    )


def _not_approved(error: PlanNotApprovedError) -> JSONResponse:
    body = NotApprovedError(detail=NotApprovedDetail(message=str(error)))
    return JSONResponse(body.model_dump(mode="json"), status.HTTP_409_CONFLICT)


@router.post(
    "/calendar",
    response_model=CalendarExportRead,
    summary="Save the approved plan in a separate Google Calendar",
    description=(
        'Creates the calendar "TuttiTrip: <trip>" in the caller\'s Google account '
        "(scope `calendar.app.created`) with one event per stop. Saving again "
        "updates the same events instead of duplicating them. Every member may "
        "save to their own Google account."
    ),
    responses=_RESPONSES,
    dependencies=[requires(Feature.PLANNING_PLANS, Access.WRITE)],
)
async def save_calendar(
    request: Request,
    session: SessionDep,
    membership: TripMember,
    plan_id: UUID,
    google: GoogleDep,
) -> CalendarExportRead | JSONResponse:
    """Save the plan in the caller's Google Calendar.

    Args:
        request: Used to build the link to the ``.ics`` fallback.
        session: Database session.
        membership: The caller's membership of ``{trip_id}``.
        plan_id: The approved version.
        google: Google clients.

    Returns:
        What the calendar holds, or a 409 / 502 body.

    Raises:
        HTTPException: 404 when the trip has no such version.
    """
    try:
        return await export_service.export_calendar(
            session, membership, plan_id, google
        )
    except PlanNotFoundError as exc:
        raise _not_found() from exc
    except PlanNotApprovedError as exc:
        return _not_approved(exc)
    except GoogleAccessError as exc:
        return _error(
            request, membership.trip_id, plan_id, SCOPE_CALENDAR_APP_CREATED, exc
        )


@router.post(
    "/drive",
    response_model=DriveExportRead,
    summary="Export the approved plan to Google Drive",
    description=(
        "Uploads the plan day by day as a Google Doc to the caller's Drive (scope "
        "`drive.file`, so the app sees only this file). Exporting again replaces "
        "the content of the same document. The response carries the link."
    ),
    responses=_RESPONSES,
    dependencies=[requires(Feature.PLANNING_PLANS, Access.WRITE)],
)
async def export_drive(
    request: Request,
    session: SessionDep,
    membership: TripMember,
    plan_id: UUID,
    google: GoogleDep,
) -> DriveExportRead | JSONResponse:
    """Export the plan to the caller's Google Drive.

    Args:
        request: Used to build the link to the ``.ics`` fallback.
        session: Database session.
        membership: The caller's membership of ``{trip_id}``.
        plan_id: The approved version.
        google: Google clients.

    Returns:
        The file link, or a 409 / 502 body.

    Raises:
        HTTPException: 404 when the trip has no such version.
    """
    try:
        return await export_service.export_drive(session, membership, plan_id, google)
    except PlanNotFoundError as exc:
        raise _not_found() from exc
    except PlanNotApprovedError as exc:
        return _not_approved(exc)
    except GoogleAccessError as exc:
        return _error(request, membership.trip_id, plan_id, SCOPE_DRIVE_FILE, exc)


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
