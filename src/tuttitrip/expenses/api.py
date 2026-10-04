"""Expense endpoints (nested under a trip)."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from tuttitrip.expenses.schemas import (
    ExpenseCreate,
    ExpenseQuery,
    ExpenseRead,
    ExpenseUpdate,
    ExpenseValidationErrors,
)
from tuttitrip.expenses.services import expense_service
from tuttitrip.expenses.services.expense_service import (
    ExpenseForbiddenError,
    ExpenseInvalidError,
    ExpenseNotFoundError,
)
from tuttitrip.expenses.settlement.services.settlement_service import (
    SettlementClosedError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripMember

router = APIRouter(prefix="/trips/{trip_id}/expenses", tags=["expenses"])

EXPENSE_NOT_FOUND = "Expense not found"
INVALID_EXPENSE: dict[int | str, dict[str, Any]] = {
    409: {"description": "The settlement is closed; the host must reopen it."},
    422: {
        "model": ExpenseValidationErrors,
        "description": "An expense rule is broken.",
    },
}


def _closed(exc: SettlementClosedError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


def _invalid(exc: ExpenseInvalidError) -> HTTPException:
    detail = [
        {"type": v.code.value, "loc": ["body", v.field], "msg": v.message}
        for v in exc.violations
    ]
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


@router.get("", dependencies=[requires(Feature.EXPENSES_CORE, Access.READ)])
async def list_expenses(
    membership: TripMember,
    session: SessionDep,
    query: Annotated[ExpenseQuery, Query()],
) -> Page[ExpenseRead]:
    """List the expenses of a trip: paginated, filterable and sortable.

    Filters: `date_from`/`date_to` (day spent, inclusive), `payer_profile_id`,
    `participant_profile_id` and `category`. Sort by `spent_on` (default,
    newest first), `amount` or `created_at`.

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.
        query: Paging, sorting and filters.

    Returns:
        One page of the trip's expenses.
    """
    return await expense_service.list_expenses(session, membership, query)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses=INVALID_EXPENSE,
    dependencies=[requires(Feature.EXPENSES_CORE, Access.WRITE)],
)
async def create_expense(
    data: ExpenseCreate, membership: TripMember, session: SessionDep
) -> ExpenseRead:
    """Add an expense; the caller becomes its author.

    The payer and participants are trip profiles. A person left out of
    `participants` does not pay. A broken rule answers 422 with an
    `ExpenseErrorCode` in `type`.

    Args:
        data: The expense.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The created expense.
    """
    try:
        return await expense_service.create_expense(session, membership, data)
    except ExpenseInvalidError as exc:
        raise _invalid(exc) from exc
    except SettlementClosedError as exc:
        raise _closed(exc) from exc


@router.patch(
    "/{expense_id}",
    responses=INVALID_EXPENSE,
    dependencies=[requires(Feature.EXPENSES_CORE, Access.WRITE)],
)
async def update_expense(
    expense_id: UUID, data: ExpenseUpdate, membership: TripMember, session: SessionDep
) -> ExpenseRead:
    """Change an expense; only the author, a co-host or the host may.

    Send only what changes. `participants` replaces the whole list; the merged
    expense must pass the same rules as on creation.

    Args:
        expense_id: Expense to change.
        data: Fields to change.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The changed expense.
    """
    try:
        return await expense_service.update_expense(
            session, membership, expense_id, data
        )
    except ExpenseNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, EXPENSE_NOT_FOUND) from exc
    except ExpenseForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ExpenseInvalidError as exc:
        raise _invalid(exc) from exc
    except SettlementClosedError as exc:
        raise _closed(exc) from exc


@router.delete(
    "/{expense_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={409: INVALID_EXPENSE[409]},
    dependencies=[requires(Feature.EXPENSES_CORE, Access.WRITE)],
)
async def delete_expense(
    expense_id: UUID, membership: TripMember, session: SessionDep
) -> Response:
    """Delete an expense; only the author, a co-host or the host may.

    Args:
        expense_id: Expense to delete.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        An empty 204 response.
    """
    try:
        await expense_service.delete_expense(session, membership, expense_id)
    except ExpenseNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, EXPENSE_NOT_FOUND) from exc
    except ExpenseForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except SettlementClosedError as exc:
        raise _closed(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
