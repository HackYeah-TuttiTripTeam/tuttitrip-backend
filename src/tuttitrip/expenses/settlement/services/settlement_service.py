"""Compute balances."""

from tuttitrip.expenses.settlement.logic.balances import net_balances
from tuttitrip.expenses.settlement.schemas import BalancesRequest, BalancesResponse


def compute_balances(request: BalancesRequest) -> BalancesResponse:
    """Net balance per person.

    Args:
        request: Payments to settle.

    Returns:
        The balances.
    """
    return BalancesResponse(balances=net_balances(request.payments))
