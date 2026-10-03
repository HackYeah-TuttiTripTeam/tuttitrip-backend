"""Read trip expenses."""

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.expenses import db
from tuttitrip.expenses.schemas import ExpenseRead
from tuttitrip.trips.schemas import TripMembership


async def list_expenses(
    session: AsyncSession, membership: TripMembership
) -> list[ExpenseRead]:
    """List expenses of a trip.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.

    Returns:
        The trip's expenses.
    """
    expenses = await db.select_expenses_by_trip(session, membership.trip_id)
    return [ExpenseRead.model_validate(expense) for expense in expenses]
