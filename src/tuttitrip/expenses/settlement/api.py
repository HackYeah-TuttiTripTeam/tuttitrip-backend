"""Settlement endpoints."""

from fastapi import APIRouter

from tuttitrip.expenses.settlement.schemas import BalancesRequest, BalancesResponse
from tuttitrip.expenses.settlement.services import settlement_service

router = APIRouter(prefix="/expenses/settlement", tags=["expenses"])


@router.post("/balances")
def balances(request: BalancesRequest) -> BalancesResponse:
    """Compute net balances for a set of payments.

    Args:
        request: Payments to settle.

    Returns:
        Balance per person.
    """
    return settlement_service.compute_balances(request)
