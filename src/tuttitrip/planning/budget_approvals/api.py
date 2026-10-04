"""Consent to exceed the budget (nested under a trip)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse

from tuttitrip.planning.budget_approvals.schemas import (
    BudgetApprovalQuery,
    BudgetApprovalRead,
    BudgetDecisionCreate,
    NotPendingDetail,
    NotPendingError,
)
from tuttitrip.planning.budget_approvals.services import approval_service
from tuttitrip.planning.budget_approvals.services.approval_service import (
    ApprovalNotFoundError,
    ApprovalNotPendingError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripHost, TripMember

router = APIRouter(prefix="/trips/{trip_id}/budget-approvals", tags=["planning"])

NOT_FOUND = {404: {"description": "Trip or approval not found."}}
NOT_PENDING = {
    409: {
        "model": NotPendingError,
        "description": "Decided already, or the plan was recomputed (superseded).",
    }
}


def _not_pending(error: ApprovalNotPendingError) -> JSONResponse:
    body = NotPendingError(
        detail=NotPendingDetail(message=str(error), status=error.status)
    )
    return JSONResponse(body.model_dump(mode="json"), status.HTTP_409_CONFLICT)


@router.get(
    "",
    summary="Consent questions about exceeding the budget",
    description=(
        "Paged, newest first by default; filter by `status`. A question opens with "
        "a plan that needs approval (`budget.needs_approval`): the amount over "
        "`B_do`, the price per point (`kappa`) and who gains most. Members see "
        "`P_flex` marked as waiting for the host until it is decided."
    ),
    dependencies=[requires(Feature.PLANNING_PLANS, Access.READ)],
)
async def list_budget_approvals(
    session: SessionDep,
    membership: TripMember,
    query: Annotated[BudgetApprovalQuery, Query()],
) -> Page[BudgetApprovalRead]:
    """One page of the questions.

    Args:
        session: Database session.
        membership: The caller's membership of ``{trip_id}``.
        query: Page, sort and filters.

    Returns:
        The page.
    """
    return await approval_service.list_approvals(session, membership, query)


@router.post(
    "/{approval_id}/approve",
    response_model=BudgetApprovalRead,
    summary="Approve exceeding the budget",
    description=(
        "Only the host. `P_flex` stays the plan in force. The decision goes to "
        "the append-only log with the amount over `B_do`, `kappa`, the person "
        "who gains most and the author."
    ),
    responses={**NOT_FOUND, **NOT_PENDING},
    dependencies=[requires(Feature.PLANNING_PLANS, Access.WRITE)],
)
async def approve_budget(
    session: SessionDep,
    membership: TripHost,
    approval_id: UUID,
    data: BudgetDecisionCreate | None = None,
) -> BudgetApprovalRead | JSONResponse:
    """Approve.

    Args:
        session: Database session.
        membership: The host's membership of ``{trip_id}``.
        approval_id: The question.
        data: Optional reason.

    Returns:
        The decided question, or a 409 body.

    Raises:
        HTTPException: 404 when the trip has no such question.
    """
    try:
        return await approval_service.approve(
            session, membership, approval_id, data or BudgetDecisionCreate()
        )
    except ApprovalNotPendingError as exc:
        return _not_pending(exc)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found") from exc


@router.post(
    "/{approval_id}/reject",
    response_model=BudgetApprovalRead,
    summary="Refuse to exceed the budget",
    description=(
        "Only the host. `P_strict`, within `B_do`, is stored as the newest plan "
        "version and becomes the plan in force. The decision is logged like an "
        "approval."
    ),
    responses={**NOT_FOUND, **NOT_PENDING},
    dependencies=[requires(Feature.PLANNING_PLANS, Access.WRITE)],
)
async def reject_budget(
    session: SessionDep,
    membership: TripHost,
    approval_id: UUID,
    data: BudgetDecisionCreate | None = None,
) -> BudgetApprovalRead | JSONResponse:
    """Reject.

    Args:
        session: Database session.
        membership: The host's membership of ``{trip_id}``.
        approval_id: The question.
        data: Optional reason.

    Returns:
        The decided question, or a 409 body.

    Raises:
        HTTPException: 404 when the trip has no such question.
    """
    try:
        return await approval_service.reject(
            session, membership, approval_id, data or BudgetDecisionCreate()
        )
    except ApprovalNotPendingError as exc:
        return _not_pending(exc)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found") from exc
