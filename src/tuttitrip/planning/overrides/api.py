"""Host overrides and the decision log (nested under a trip)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse

from tuttitrip.planning.overrides.schemas import (
    DecisionQuery,
    DecisionRead,
    OverrideConflict,
    OverrideCreate,
    OverridePreview,
    OverrideRead,
)
from tuttitrip.planning.overrides.services import override_service
from tuttitrip.planning.overrides.services.override_service import (
    OverrideConflictError,
    OverrideNotFoundError,
)
from tuttitrip.planning.plans.services.plan_service import PlanInputError
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripHost, TripMember

router = APIRouter(prefix="/trips/{trip_id}", tags=["planning"])

CONFLICT = {
    409: {
        "model": OverrideConflict,
        "description": 'A "must" runs into a veto or another hard rule (E0).',
    }
}
INPUT = {422: {"description": "The trip lacks dates, a city or people."}}


def _conflict(error: OverrideConflictError) -> JSONResponse:
    body = OverrideConflict(detail=str(error), conflicts=error.conflicts)
    return JSONResponse(body.model_dump(mode="json"), status.HTTP_409_CONFLICT)


@router.post(
    "/overrides/preview",
    response_model=OverridePreview,
    summary="What a host decision would cost, nothing stored",
    description=(
        "Computes the plan with and without the decision and returns the change of "
        "`min r`, Jain's index, `r` per person, cost and active time. The solo "
        "plans are not recomputed (their reference points are reused)."
    ),
    responses={**CONFLICT, **INPUT},
    dependencies=[requires(Feature.PLANNING_PLANS, Access.WRITE)],
)
async def preview_override(
    session: SessionDep, membership: TripHost, data: OverrideCreate
) -> OverridePreview | JSONResponse:
    """Preview a decision.

    Args:
        session: Database session.
        membership: The host's membership of ``{trip_id}``.
        data: The decision.

    Returns:
        The effects, or a 409 body when a "must" cannot be honoured.

    Raises:
        HTTPException: 422 when the trip cannot be planned yet.
    """
    try:
        return await override_service.preview(session, membership, data)
    except OverrideConflictError as exc:
        return _conflict(exc)
    except PlanInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post(
    "/overrides",
    status_code=status.HTTP_201_CREATED,
    response_model=OverrideRead,
    summary="Force a place into the plan or block it",
    description=(
        "Stores the decision (a hard constraint of the next plan) and one entry of "
        "the decision log with the same numbers as the preview. Only the host may "
        "do it; call `POST .../plans` to recompute."
    ),
    responses={**CONFLICT, **INPUT},
    dependencies=[requires(Feature.PLANNING_PLANS, Access.WRITE)],
)
async def create_override(
    session: SessionDep, membership: TripHost, data: OverrideCreate
) -> OverrideRead | JSONResponse:
    """Store a decision.

    Args:
        session: Database session.
        membership: The host's membership of ``{trip_id}``.
        data: The decision.

    Returns:
        The decision with its logged effects, or a 409 body.

    Raises:
        HTTPException: 422 when the trip cannot be planned yet.
    """
    try:
        return await override_service.create_override(session, membership, data)
    except OverrideConflictError as exc:
        return _conflict(exc)
    except PlanInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.delete(
    "/overrides/{override_id}",
    summary="Take a host decision back",
    responses={404: {"description": "No such decision on this trip."}, **INPUT},
    dependencies=[requires(Feature.PLANNING_PLANS, Access.WRITE)],
)
async def revoke_override(
    session: SessionDep, membership: TripHost, override_id: UUID
) -> OverrideRead:
    """Revoke a decision; the log records what that changed.

    Args:
        session: Database session.
        membership: The host's membership of ``{trip_id}``.
        override_id: The decision.

    Returns:
        The revoked decision.

    Raises:
        HTTPException: 404 when the trip has no such decision, 422 when the
            trip cannot be planned.
    """
    try:
        return await override_service.revoke_override(session, membership, override_id)
    except OverrideNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Override not found") from exc
    except PlanInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get(
    "/decisions",
    summary="Log of host decisions",
    description="Append-only; newest first by default. Paged, filter by `kind`.",
    dependencies=[requires(Feature.PLANNING_PLANS, Access.READ)],
)
async def list_decisions(
    session: SessionDep,
    membership: TripMember,
    query: Annotated[DecisionQuery, Query()],
) -> Page[DecisionRead]:
    """One page of the decision log.

    Args:
        session: Database session.
        membership: The caller's membership of ``{trip_id}``.
        query: Page, sort and filters.

    Returns:
        The page.
    """
    return await override_service.list_decisions(session, membership, query)
