"""Settlement queries on PostgreSQL."""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.expenses.settlement.models import SettlementPayment, TripSettlement
from tuttitrip.expenses.settlement.schemas import (
    PaymentFilters,
    PaymentQuery,
    PaymentSort,
)
from tuttitrip.shared.db.pagination import Column, ordering, paginate
from tuttitrip.shared.pagination.schemas import Page

COLUMNS: dict[PaymentSort, Column] = {
    PaymentSort.PAID_ON: SettlementPayment.paid_on,
    PaymentSort.AMOUNT: SettlementPayment.amount,
    PaymentSort.CREATED_AT: SettlementPayment.created_at,
}


def scoped(trip_id: UUID) -> Select[SettlementPayment]:
    """Select the payments of one trip (the caller scope).

    Args:
        trip_id: Trip the caller was checked for.

    Returns:
        The select every list starts from.
    """
    return select(SettlementPayment).where(SettlementPayment.trip_id == trip_id)


def apply_filters(
    stmt: Select[SettlementPayment], filters: PaymentFilters
) -> Select[SettlementPayment]:
    """Add the list filters to a select.

    Args:
        stmt: A select of payments.
        filters: The request's filters.

    Returns:
        The filtered select.
    """
    if filters.from_profile_id is not None:
        stmt = stmt.where(SettlementPayment.from_profile_id == filters.from_profile_id)
    if filters.to_profile_id is not None:
        stmt = stmt.where(SettlementPayment.to_profile_id == filters.to_profile_id)
    return stmt


async def select_page(
    session: AsyncSession, trip_id: UUID, query: PaymentQuery
) -> Page[SettlementPayment]:
    """One page of a trip's payments.

    Args:
        session: Open session.
        trip_id: Trip id.
        query: Paging, sorting and filters.

    Returns:
        The page.
    """
    return await paginate(
        session,
        apply_filters(scoped(trip_id), query),
        query,
        ordering(COLUMNS, query.sort, SettlementPayment.id),
    )


async def select_all_payments(
    session: AsyncSession, trip_id: UUID
) -> list[SettlementPayment]:
    """All payments of a trip, for the computation.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The payments in a stable order.
    """
    result = await session.scalars(scoped(trip_id).order_by(SettlementPayment.id))
    return list(result)


async def select_payment(
    session: AsyncSession, trip_id: UUID, payment_id: UUID
) -> SettlementPayment | None:
    """Find one payment of a trip.

    Args:
        session: Open session.
        trip_id: Trip id.
        payment_id: Payment id.

    Returns:
        The payment, or None when it is not on this trip.
    """
    return await session.scalar(
        scoped(trip_id).where(SettlementPayment.id == payment_id)
    )


async def insert_payment(session: AsyncSession, payment: SettlementPayment) -> None:
    """Add a payment and flush.

    Args:
        session: Open session (caller commits).
        payment: The new payment.
    """
    session.add(payment)
    await session.flush()


async def delete_payment(session: AsyncSession, payment: SettlementPayment) -> None:
    """Delete a payment and flush.

    Args:
        session: Open session (caller commits).
        payment: The payment to remove.
    """
    await session.delete(payment)
    await session.flush()


async def select_closure(session: AsyncSession, trip_id: UUID) -> TripSettlement | None:
    """The closure record of a trip.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The record, or None while the settlement is open.
    """
    return await session.get(TripSettlement, trip_id)


async def insert_closure(session: AsyncSession, closure: TripSettlement) -> None:
    """Close the settlement and flush.

    Args:
        session: Open session (caller commits).
        closure: The record.
    """
    session.add(closure)
    await session.flush()


async def delete_closure(session: AsyncSession, closure: TripSettlement) -> None:
    """Reopen the settlement and flush.

    Args:
        session: Open session (caller commits).
        closure: The record to remove.
    """
    await session.delete(closure)
    await session.flush()
