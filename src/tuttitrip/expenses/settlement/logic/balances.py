"""Net balances from equally split payments."""

from collections import defaultdict
from decimal import Decimal

from tuttitrip.expenses.settlement.schemas import Payment


def net_balances(payments: list[Payment]) -> dict[str, Decimal]:
    """Credit each payer and debit each participant's equal share.

    Args:
        payments: Payments to settle.

    Returns:
        Balance per person; the values sum to zero (up to rounding).
    """
    balances: defaultdict[str, Decimal] = defaultdict(Decimal)
    for payment in payments:
        share = payment.amount / len(payment.participants)
        balances[payment.payer] += payment.amount
        for person in payment.participants:
            balances[person] -= share
    return dict(balances)
