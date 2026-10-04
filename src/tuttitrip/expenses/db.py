"""Expense queries on PostgreSQL."""

from uuid import UUID

from sqlalchemy import Select, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.expenses.models import Expense, ExpenseShare
from tuttitrip.expenses.schemas import ExpenseFilters, ExpenseQuery, ExpenseSort
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
