"""Read trip expenses."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.expenses import db
from tuttitrip.expenses.schemas import ExpenseRead
from tuttitrip.trips.services import trip_service


async def list_expenses(
    session: AsyncSession, trip_id: UUID, owner_sub: str
) -> list[ExpenseRead]:
    """List expenses of a trip the caller owns.

    Args:
        session: Open session.
        trip_id: Trip id.
        owner_sub: Auth0 subject of the caller.

    Returns:
        The trip's expenses.
    """
    await trip_service.get_owned_trip(session, trip_id, owner_sub)
    expenses = await db.select_expenses_by_trip(session, trip_id)
    return [ExpenseRead.model_validate(expense) for expense in expenses]
