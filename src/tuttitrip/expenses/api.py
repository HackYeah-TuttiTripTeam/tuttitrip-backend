"""Expense endpoints (nested under a trip)."""

from fastapi import APIRouter

from tuttitrip.expenses.schemas import ExpenseRead
from tuttitrip.expenses.services import expense_service
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripMember

router = APIRouter(prefix="/trips/{trip_id}/expenses", tags=["expenses"])


@router.get("", dependencies=[requires(Feature.EXPENSES_CORE, Access.READ)])
async def list_expenses(
    membership: TripMember, session: SessionDep
) -> list[ExpenseRead]:
    """List expenses of a trip the caller belongs to.

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The trip's expenses.
    """
    return await expense_service.list_expenses(session, membership)
