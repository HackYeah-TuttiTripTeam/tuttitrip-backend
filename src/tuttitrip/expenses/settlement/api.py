"""Settlement endpoints."""

from fastapi import APIRouter

from tuttitrip.expenses.settlement.schemas import BalancesRequest, BalancesResponse
from tuttitrip.expenses.settlement.services import settlement_service
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature

router = APIRouter(prefix="/expenses/settlement", tags=["expenses"])


@router.post(
    "/balances", dependencies=[requires(Feature.EXPENSES_SETTLEMENT, Access.READ)]
)
def balances(request: BalancesRequest) -> BalancesResponse:
    """Compute net balances for a set of payments.

    Args:
        request: Payments to settle.

    Returns:
        Balance per person.
    """
    return settlement_service.compute_balances(request)
