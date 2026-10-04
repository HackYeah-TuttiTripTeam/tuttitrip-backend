"""Settlement endpoints (nested under a trip)."""

from fastapi import APIRouter

from tuttitrip.expenses.settlement.schemas import SettlementRead
from tuttitrip.expenses.settlement.services import settlement_service
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripMember

router = APIRouter(prefix="/trips/{trip_id}/expenses/settlement", tags=["expenses"])


@router.get("", dependencies=[requires(Feature.EXPENSES_SETTLEMENT, Access.READ)])
async def get_settlement(membership: TripMember, session: SessionDep) -> SettlementRead:
    """Balance of every person and the smallest list of transfers.

    Equal, percent and weight splits are computed to the cent (largest
    remainder, ties by profile id), so the balances add up to 0.00. The list of
    transfers does not depend on the order of the expenses.

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        Balances, transfers and the total spent.
    """
    return await settlement_service.get_settlement(session, membership)
