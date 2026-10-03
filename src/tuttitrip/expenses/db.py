"""Expense queries on PostgreSQL."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.expenses.models import Expense


async def select_expenses_by_trip(
    session: AsyncSession, trip_id: UUID
) -> Sequence[Expense]:
    """List a trip's expenses, oldest first.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The trip's expenses.
    """
    result = await session.scalars(
        select(Expense).where(Expense.trip_id == trip_id).order_by(Expense.created_at)
    )
    return result.all()
