"""Settlement endpoints (nested under a trip)."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from tuttitrip.expenses.settlement.schemas import (
    PaymentCreate,
    PaymentQuery,
    PaymentRead,
    PaymentValidationErrors,
    SettlementRead,
)
from tuttitrip.expenses.settlement.services import settlement_service
from tuttitrip.expenses.settlement.services.settlement_service import (
    PaymentForbiddenError,
    PaymentInvalidError,
    PaymentNotFoundError,
    SettlementClosedError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripHost, TripMember

router = APIRouter(prefix="/trips/{trip_id}/expenses/settlement", tags=["expenses"])

CLOSED: dict[int | str, dict[str, Any]] = {
    409: {"description": "The settlement is closed; the host must reopen it."}
}
INVALID_PAYMENT: dict[int | str, dict[str, Any]] = {
    422: {"model": PaymentValidationErrors, "description": "A payment rule is broken."}
}


def _closed(exc: SettlementClosedError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.get("", dependencies=[requires(Feature.EXPENSES_SETTLEMENT, Access.READ)])
async def get_settlement(membership: TripMember, session: SessionDep) -> SettlementRead:
    """Balance of every person and the smallest list of transfers still to pay.

    Equal, percent and weight splits are computed to the cent (largest
    remainder, ties by profile id), so the balances add up to 0.00. Payments
    marked as paid are counted, so a paid transfer disappears. The list does not
    depend on the order of the expenses.

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        Balances, transfers and the total spent.
    """
    return await settlement_service.get_settlement(session, membership)


@router.get(
    "/payments", dependencies=[requires(Feature.EXPENSES_SETTLEMENT, Access.READ)]
)
async def list_payments(
    membership: TripMember,
    session: SessionDep,
    query: Annotated[PaymentQuery, Query()],
) -> Page[PaymentRead]:
    """List the payments marked as made: paginated, filterable and sortable.

    Filters: `from_profile_id` and `to_profile_id`. Sort by `paid_on` (default,
    newest first), `amount` or `created_at`.

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.
        query: Paging, sorting and filters.

    Returns:
        One page of payments.
    """
    return await settlement_service.list_payments(session, membership, query)


@router.post(
    "/payments",
    status_code=status.HTTP_201_CREATED,
    responses=CLOSED | INVALID_PAYMENT,
    dependencies=[requires(Feature.EXPENSES_CORE, Access.WRITE)],
)
async def mark_paid(
    data: PaymentCreate, membership: TripMember, session: SessionDep
) -> PaymentRead:
    """Mark a transfer, or a part of it, as paid.

    The payer, the receiver or the host may do it (403 for other members). A
    part leaves the rest of the transfer; more than the debt turns the balance
    around. A closed settlement answers 409.

    Args:
        data: Who paid whom, how much and when.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The stored payment.
    """
    try:
        return await settlement_service.mark_paid(session, membership, data)
    except SettlementClosedError as exc:
        raise _closed(exc) from exc
    except PaymentForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except PaymentInvalidError as exc:
        detail = [
            {"type": exc.code.value, "loc": ["body", exc.field], "msg": exc.message}
        ]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail) from exc


@router.delete(
    "/payments/{payment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=CLOSED,
    dependencies=[requires(Feature.EXPENSES_CORE, Access.WRITE)],
)
async def remove_payment(
    payment_id: UUID, membership: TripMember, session: SessionDep
) -> Response:
    """Remove a payment marked by mistake; the payer, the receiver or the host.

    Args:
        payment_id: Payment to remove.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        An empty 204 response.
    """
    try:
        await settlement_service.remove_payment(session, membership, payment_id)
    except PaymentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found") from exc
    except SettlementClosedError as exc:
        raise _closed(exc) from exc
    except PaymentForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/close", dependencies=[requires(Feature.EXPENSES_CORE, Access.WRITE)])
async def close_settlement(membership: TripHost, session: SessionDep) -> SettlementRead:
    """Close the settlement after checking the expenses (host only).

    From then on adding, changing or deleting an expense, and marking or
    removing a payment, answers 409. Closing again changes nothing.

    Args:
        membership: The caller's membership, checked to be the host.
        session: Database session.

    Returns:
        The settlement with `closed_at` set.
    """
    return await settlement_service.close(session, membership)


@router.post("/reopen", dependencies=[requires(Feature.EXPENSES_CORE, Access.WRITE)])
async def reopen_settlement(
    membership: TripHost, session: SessionDep
) -> SettlementRead:
    """Reopen a closed settlement (host only).

    Args:
        membership: The caller's membership, checked to be the host.
        session: Database session.

    Returns:
        The settlement with `closed_at` empty.
    """
    return await settlement_service.reopen(session, membership)
