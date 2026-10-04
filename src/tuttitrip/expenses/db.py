"""Expense queries on PostgreSQL."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.expenses.models import Expense, ExpenseEvidence, ExpenseShare
from tuttitrip.expenses.schemas import (
    ExpenseDayTotal,
    ExpenseFilters,
    ExpenseQuery,
    ExpenseSort,
    ExpenseStatus,
)
from tuttitrip.expenses.settlement.models import SettlementPayment
from tuttitrip.shared.db.pagination import Column, ordering, paginate
from tuttitrip.shared.pagination.schemas import Page

COLUMNS: dict[ExpenseSort, Column] = {
    ExpenseSort.SPENT_ON: Expense.spent_on,
    ExpenseSort.AMOUNT: Expense.amount,
    ExpenseSort.CREATED_AT: Expense.created_at,
}


def scoped(trip_id: UUID) -> Select[Expense]:
    """Select the expenses of one trip (the caller scope).

    Args:
        trip_id: Trip the caller was checked for.

    Returns:
        The select every list and bulk operation starts from.
    """
    return select(Expense).where(Expense.trip_id == trip_id)


def apply_filters(stmt: Select[Expense], filters: ExpenseFilters) -> Select[Expense]:
    """Add the list filters to a select.

    Args:
        stmt: A select of expenses.
        filters: The request's filters.

    Returns:
        The filtered select (participants are matched with ``EXISTS``).
    """
    if filters.date_from is not None:
        stmt = stmt.where(Expense.spent_on >= filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(Expense.spent_on <= filters.date_to)
    if filters.payer_profile_id is not None:
        stmt = stmt.where(Expense.payer_profile_id == filters.payer_profile_id)
    if filters.status is not None:
        stmt = stmt.where(Expense.status == filters.status)
    if filters.category is not None:
        stmt = stmt.where(Expense.category == filters.category)
    if filters.participant_profile_id is not None:
        stmt = stmt.where(
            exists().where(
                ExpenseShare.expense_id == Expense.id,
                ExpenseShare.profile_id == filters.participant_profile_id,
            )
        )
    return stmt


async def select_page(
    session: AsyncSession, trip_id: UUID, query: ExpenseQuery
) -> Page[Expense]:
    """One page of a trip's expenses.

    Args:
        session: Open session.
        trip_id: Trip id.
        query: Paging, sorting and filters.

    Returns:
        The page, with shares loaded.
    """
    return await paginate(
        session,
        apply_filters(scoped(trip_id), query),
        query,
        ordering(COLUMNS, query.sort, Expense.id),
    )


async def select_expense(
    session: AsyncSession, trip_id: UUID, expense_id: UUID
) -> Expense | None:
    """Find one expense of a trip.

    Args:
        session: Open session.
        trip_id: Trip id.
        expense_id: Expense id.

    Returns:
        The expense with shares, or None when it is not on this trip.
    """
    return await session.scalar(scoped(trip_id).where(Expense.id == expense_id))


async def insert_expense(session: AsyncSession, expense: Expense) -> None:
    """Add an expense with its shares and flush.

    Args:
        session: Open session (caller commits).
        expense: The new expense.
    """
    session.add(expense)
    await session.flush()


async def replace_shares(
    session: AsyncSession, expense: Expense, shares: list[ExpenseShare]
) -> None:
    """Replace the participants of an expense and flush.

    Args:
        session: Open session (caller commits).
        expense: The expense.
        shares: The new participants.
    """
    expense.shares = shares  # delete-orphan drops the old rows
    await session.flush()


async def delete_expense(session: AsyncSession, expense: Expense) -> None:
    """Delete an expense (its shares go with it) and flush.

    Args:
        session: Open session (caller commits).
        expense: The expense to remove.
    """
    await session.delete(expense)
    await session.flush()


async def profile_has_expenses(
    session: AsyncSession, trip_id: UUID, profile_id: UUID
) -> bool:
    """Tell whether a person paid or shares an expense, or is in a settlement payment.

    Args:
        session: Open session.
        trip_id: Trip id.
        profile_id: Profile id.

    Returns:
        True when removing the profile would orphan an expense or a payment.
    """
    shared = exists().where(
        ExpenseShare.expense_id == Expense.id, ExpenseShare.profile_id == profile_id
    )
    paid = exists().where(
        SettlementPayment.trip_id == trip_id,
        (SettlementPayment.from_profile_id == profile_id)
        | (SettlementPayment.to_profile_id == profile_id),
    )
    found = await session.scalar(
        select(
            exists().where(
                Expense.trip_id == trip_id,
                (Expense.payer_profile_id == profile_id) | shared,
            )
            | paid
        )
    )
    return bool(found)


async def select_all(session: AsyncSession, trip_id: UUID) -> list[Expense]:
    """All confirmed expenses of a trip, for settlement (not a list).

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The expenses with shares, in a stable order.
    """
    result = await session.scalars(
        scoped(trip_id)
        .where(Expense.status == ExpenseStatus.CONFIRMED)
        .order_by(Expense.id)
    )
    return list(result)


async def insert_evidence(session: AsyncSession, evidence: ExpenseEvidence) -> None:
    """Store a receipt image and flush.

    Args:
        session: Open session (caller commits).
        evidence: The image row.
    """
    session.add(evidence)
    await session.flush()


async def select_evidence(
    session: AsyncSession, trip_id: UUID, evidence_id: UUID
) -> ExpenseEvidence | None:
    """Find a receipt image of a trip.

    Args:
        session: Open session.
        trip_id: Trip id.
        evidence_id: Evidence id.

    Returns:
        The row, or None when it is not on this trip (or already deleted).
    """
    return await session.scalar(
        select(ExpenseEvidence).where(
            ExpenseEvidence.id == evidence_id, ExpenseEvidence.trip_id == trip_id
        )
    )


async def select_by_evidence(
    session: AsyncSession, trip_id: UUID, evidence_id: UUID
) -> Expense | None:
    """The draft expense read from a receipt, if it was created.

    Args:
        session: Open session.
        trip_id: Trip id.
        evidence_id: Evidence id.

    Returns:
        The expense, or None.
    """
    return await session.scalar(
        scoped(trip_id).where(Expense.evidence_id == evidence_id)
    )


async def delete_evidence(session: AsyncSession, evidence_id: UUID) -> None:
    """Delete a receipt image (a draft keeps no link) and flush.

    Args:
        session: Open session (caller commits).
        evidence_id: Evidence id.
    """
    await session.execute(
        delete(ExpenseEvidence).where(ExpenseEvidence.id == evidence_id)
    )
    await session.flush()


async def count_drafts(session: AsyncSession, trip_id: UUID) -> int:
    """Number of draft expenses of a trip.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        How many are waiting for confirmation.
    """
    return (
        await session.scalar(
            select(func.count())
            .select_from(Expense)
            .where(Expense.trip_id == trip_id, Expense.status == ExpenseStatus.DRAFT)
        )
        or 0
    )


async def count_evidence(session: AsyncSession, trip_id: UUID) -> int:
    """Number of stored receipt images of a trip (all are unconfirmed).

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The count.
    """
    return (
        await session.scalar(
            select(func.count())
            .select_from(ExpenseEvidence)
            .where(ExpenseEvidence.trip_id == trip_id)
        )
        or 0
    )


async def delete_expired_evidence(session: AsyncSession, now: datetime) -> None:
    """Delete receipt images past their ``delete_after`` (all trips) and flush.

    Args:
        session: Open session (caller commits).
        now: The current time.
    """
    await session.execute(
        delete(ExpenseEvidence).where(ExpenseEvidence.delete_after < now)
    )
    await session.flush()


async def select_day_totals(
    session: AsyncSession, trip_id: UUID
) -> list[ExpenseDayTotal]:
    """Sum the trip's confirmed expenses per day and category.

    Drafts do not count. Every expense counts in full on its day, in the trip
    currency (``trip_amount``): the budget of a day is about what the group
    paid, not about each person's share.

    Args:
        session: Open session.
        trip_id: Trip the caller was checked for.

    Returns:
        One row per day and category, oldest day first.
    """
    stmt = (
        select(Expense.spent_on, Expense.category, func.sum(Expense.trip_amount))
        .where(Expense.trip_id == trip_id, Expense.status == ExpenseStatus.CONFIRMED)
        .group_by(Expense.spent_on, Expense.category)
        .order_by(Expense.spent_on, Expense.category)
    )
    rows = (await session.execute(stmt)).all()
    return [
        ExpenseDayTotal(spent_on=d, category=c, amount=a or Decimal(0))
        for d, c, a in rows
    ]
