"""Settlement of a trip: balances and transfers from its expenses."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.expenses import db
from tuttitrip.expenses.logic.split import CENTS, Share, to_cents
from tuttitrip.expenses.models import Expense
from tuttitrip.expenses.settlement.logic.balances import (
    Spending,
    net_balances,
    settle,
)
from tuttitrip.expenses.settlement.schemas import (
    BalanceRead,
    SettlementRead,
    TransferRead,
)
from tuttitrip.profiles.services import profile_service
from tuttitrip.trips.schemas import TripMembership
from tuttitrip.trips.services import trip_service


def _money(cents: int) -> Decimal:
    return Decimal(cents) / CENTS


def _spending(expense: Expense) -> Spending:
    return Spending(
        payer=expense.payer_profile_id,
        cents=to_cents(expense.trip_amount),
        method=expense.split_method,
        shares=[Share(s.profile_id, s.value) for s in expense.shares],
    )


async def get_settlement(
    session: AsyncSession, membership: TripMembership
) -> SettlementRead:
    """Balances and the smallest list of transfers of the trip.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.

    Returns:
        The settlement, every amount in cents-exact decimals.
    """
    trip = await trip_service.get_trip(session, membership)
    profiles = await profile_service.list_profiles(session, membership)
    expenses = await db.select_all(session, membership.trip_id)
    spendings = [_spending(e) for e in expenses]
    balances: dict[UUID, int] = {
        **dict.fromkeys((p.id for p in profiles), 0),
        **net_balances(spendings),
    }
    return SettlementRead(
        currency=trip.currency,
        total_spent=_money(sum(s.cents for s in spendings)),
        balances=[
            BalanceRead(profile_id=p, amount=_money(balances[p]))
            for p in sorted(balances, key=lambda p: p.int)
        ],
        transfers=[
            TransferRead(
                from_profile_id=t.from_profile_id,
                to_profile_id=t.to_profile_id,
                amount=_money(t.cents),
            )
            for t in settle(balances)
        ],
    )
