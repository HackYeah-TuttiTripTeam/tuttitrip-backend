"""Expense endpoints (nested under a trip)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from tuttitrip.expenses.schemas import ExpenseRead
from tuttitrip.expenses.services import expense_service
from tuttitrip.shared.auth.api import CurrentUser
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.trips.services.trip_service import TripNotFoundError

router = APIRouter(prefix="/trips/{trip_id}/expenses", tags=["expenses"])


@router.get("")
async def list_expenses(
    trip_id: UUID, user: CurrentUser, session: SessionDep
) -> list[ExpenseRead]:
    """List expenses of one of the caller's trips.

    Args:
        trip_id: Trip id.
        user: The authenticated organizer.
        session: Database session.

    Returns:
        The trip's expenses.
    """
    try:
        return await expense_service.list_expenses(session, trip_id, user.sub)
    except TripNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trip not found") from exc
