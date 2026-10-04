"""Expense endpoints (nested under a trip)."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from tuttitrip.expenses.schemas import (
    ExpenseCreate,
    ExpenseDraftState,
    ExpenseQuery,
    ExpenseRead,
    ExpenseTextRequest,
    ExpenseUpdate,
    ExpenseValidationErrors,
)
from tuttitrip.expenses.services import expense_draft_service, expense_service
from tuttitrip.expenses.services.expense_service import (
    ExpenseForbiddenError,
    ExpenseInvalidError,
    ExpenseNotFoundError,
)
from tuttitrip.expenses.settlement.services.settlement_service import (
    SettlementClosedError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.jobs.api import JobQueueDep
from tuttitrip.shared.jobs.schemas import JobAccepted
from tuttitrip.shared.jobs.services.job_queue import JobQueueUnavailableError
from tuttitrip.shared.jobs.services.worker_liveness import WorkerUnavailableError
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


def _unavailable(
    exc: WorkerUnavailableError | JobQueueUnavailableError,
) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


@router.post(
    "/draft",
    status_code=status.HTTP_202_ACCEPTED,
    responses={503: {"description": "No worker or job queue available."}},
    dependencies=[requires(Feature.EXPENSES_CORE, Access.WRITE)],
)
async def start_expense_draft(
    data: ExpenseTextRequest,
    membership: TripMember,
    session: SessionDep,
    queue: JobQueueDep,
) -> JobAccepted:
    """Start reading a typed sentence into an expense draft; nothing is saved.

    Poll `GET /trips/{trip_id}/expenses/draft/{workflow_id}` for the draft. The
    text is untrusted; the model only extracts fields.

    Args:
        data: The sentence, e.g. "obiad 142 zł, płaciła Kasia, bez Ani".
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.
        queue: Job queue.

    Returns:
        The workflow id to poll.
    """
    try:
        workflow_id = await expense_draft_service.start_draft(
            session, queue, membership, data
        )
    except (WorkerUnavailableError, JobQueueUnavailableError) as exc:
        raise _unavailable(exc) from exc
    return JobAccepted(workflow_id=workflow_id)


@router.get(
    "/draft/{workflow_id}",
    dependencies=[requires(Feature.EXPENSES_CORE, Access.READ)],
)
async def get_expense_draft(
    workflow_id: str, membership: TripMember, session: SessionDep, queue: JobQueueDep
) -> ExpenseDraftState:
    """The draft read from a sentence: payer and participants are trip profiles.

    `needs_confirmation` is true (with `issues`) when a name is not on the trip
    or is ambiguous, there is no payer or the reader was unsure.

    Args:
        workflow_id: Id from `POST .../draft`.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.
        queue: Job queue.

    Returns:
        `pending`, `failed` or `ready` with the draft.
    """
    try:
        return await expense_draft_service.get_draft(
            session, queue, membership, workflow_id
        )
    except expense_draft_service.DraftNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft not found") from exc
    except JobQueueUnavailableError as exc:
        raise _unavailable(exc) from exc
