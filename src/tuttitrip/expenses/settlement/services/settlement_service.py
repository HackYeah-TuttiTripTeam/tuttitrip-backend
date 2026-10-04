"""Settlement of a trip: balances, transfers, payments and closing."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.expenses import db as expense_db
from tuttitrip.expenses.logic.split import CENTS, Share, to_cents
from tuttitrip.expenses.models import Expense
from tuttitrip.expenses.settlement import db
from tuttitrip.expenses.settlement.logic.balances import (
    Spending,
    Transfer,
    apply_payment,
    net_balances,
    settle,
)
from tuttitrip.expenses.settlement.models import SettlementPayment, TripSettlement
from tuttitrip.expenses.settlement.schemas import (
    BalanceRead,
    PaymentCreate,
    PaymentErrorCode,
    PaymentQuery,
    PaymentRead,
    SettlementRead,
    TransferRead,
)
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service


class SettlementClosedError(Exception):
    """The host closed the settlement; expenses and payments are frozen."""


class SettlementHasDraftsError(Exception):
    """Draft expenses are waiting for confirmation, so the settlement cannot close."""

    def __init__(self, count: int) -> None:
        """Keep the number of drafts for the message.

        Args:
            count: Draft expenses of the trip.
        """
        super().__init__(
            f"{count} draft expense(s) must be confirmed or deleted before closing"
        )
        self.count = count


class PaymentNotFoundError(Exception):
    """The payment is not on this trip."""


class PaymentForbiddenError(Exception):
    """Only the payer, the receiver or the host may mark or remove a payment."""


class PaymentInvalidError(Exception):
    """The payment breaks a rule (carries the code and the field)."""

    def __init__(self, code: PaymentErrorCode, field: str, message: str) -> None:
        """Keep the violation for the API to report.

        Args:
            code: Stable error code.
            field: Request field it is about.
            message: For people.
        """
        super().__init__(message)
        self.code = code
        self.field = field
        self.message = message


def _money(cents: int) -> Decimal:
    return Decimal(cents) / CENTS


def _spending(expense: Expense) -> Spending:
    return Spending(
        payer=expense.payer_profile_id,
        cents=to_cents(expense.trip_amount or expense.amount),  # confirmed: priced
        method=expense.split_method,
        shares=[Share(s.profile_id, s.value) for s in expense.shares],
    )


def _payment_read(payment: SettlementPayment) -> PaymentRead:
    return PaymentRead.model_validate(payment, from_attributes=True)


async def ensure_open(session: AsyncSession, trip_id: UUID) -> None:
    """Lock the trip's settlement and refuse a change when it is closed.

    Call it right before a write: the lock lasts until the transaction ends, so
    a concurrent ``close`` either waits for the write or makes it fail.

    Args:
        session: Open session.
        trip_id: Trip id.

    Raises:
        SettlementClosedError: The host closed the settlement.
    """
    await db.lock_settlement(session, trip_id)
    if await db.select_closure(session, trip_id) is not None:
        msg = "The settlement is closed; ask the host to reopen it"
        raise SettlementClosedError(msg)


async def get_settlement(
    session: AsyncSession, membership: TripMembership
) -> SettlementRead:
    """Balances and the smallest list of transfers still to pay.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.

    Returns:
        The settlement, every amount cents-exact.
    """
    trip = await trip_service.get_trip(session, membership)
    profiles = await profile_service.list_profiles(session, membership)
    spendings = [
        _spending(e) for e in await expense_db.select_all(session, membership.trip_id)
    ]
    balances: dict[UUID, int] = {
        **dict.fromkeys((p.id for p in profiles), 0),
        **net_balances(spendings),
    }
    for paid in await db.select_all_payments(session, membership.trip_id):
        apply_payment(
            balances,
            Transfer(paid.from_profile_id, paid.to_profile_id, to_cents(paid.amount)),
        )
    closure = await db.select_closure(session, membership.trip_id)
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
            for t in await asyncio.to_thread(settle, balances)
        ],
        closed_at=closure.closed_at if closure else None,
    )


async def list_payments(
    session: AsyncSession, membership: TripMembership, query: PaymentQuery
) -> Page[PaymentRead]:
    """One page of the payments marked as made.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        query: Paging, sorting and filters.

    Returns:
        The page.
    """
    page = await db.select_page(session, membership.trip_id, query)
    return Page[PaymentRead](
        items=[_payment_read(p) for p in page.items],
        total=page.total,
        page=page.page,
        size=page.size,
        pages=page.pages,
    )


async def _may_act_for(
    session: AsyncSession, membership: TripMembership, *parties: UUID
) -> list[UUID]:
    """Check the caller is the host or one of ``parties``.

    Returns:
        The ids of the profiles on the trip.

    Raises:
        PaymentForbiddenError: The caller is neither.
    """
    profiles = await profile_service.list_profiles(session, membership)
    mine = {p.id for p in profiles if p.user_sub == membership.sub}
    if membership.role is not TripRole.HOST and not mine & set(parties):
        msg = "Only the payer, the receiver or the host can do this"
        raise PaymentForbiddenError(msg)
    return [p.id for p in profiles]


async def mark_paid(
    session: AsyncSession, membership: TripMembership, data: PaymentCreate
) -> PaymentRead:
    """Mark a transfer (or a part of it) as paid.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        data: Who paid whom, how much and when.

    Returns:
        The stored payment.

    Raises:
        SettlementClosedError: The settlement is closed.
        PaymentForbiddenError: The caller is neither party nor the host.
        PaymentInvalidError: Same person twice or a person not on the trip.
    """
    await ensure_open(session, membership.trip_id)
    on_trip = await _may_act_for(
        session, membership, data.from_profile_id, data.to_profile_id
    )
    if data.from_profile_id == data.to_profile_id:
        msg = "The payer and the receiver must be different people"
        raise PaymentInvalidError(PaymentErrorCode.SAME_PERSON, "to_profile_id", msg)
    for field in ("from_profile_id", "to_profile_id"):
        if getattr(data, field) not in on_trip:
            msg = "This person is not on the trip"
            raise PaymentInvalidError(PaymentErrorCode.PERSON_NOT_ON_TRIP, field, msg)
    payment = SettlementPayment(
        trip_id=membership.trip_id,
        from_profile_id=data.from_profile_id,
        to_profile_id=data.to_profile_id,
        amount=data.amount,
        paid_on=data.paid_on or datetime.now(UTC).date(),
        marked_by_sub=membership.sub,
    )
    await db.insert_payment(session, payment)
    await session.commit()
    await session.refresh(payment)
    return _payment_read(payment)


async def remove_payment(
    session: AsyncSession, membership: TripMembership, payment_id: UUID
) -> None:
    """Remove a payment marked by mistake.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        payment_id: Payment to remove.

    Raises:
        PaymentNotFoundError: The payment is not on this trip.
        SettlementClosedError: The settlement is closed.
        PaymentForbiddenError: The caller is neither party nor the host.
    """
    payment = await db.select_payment(session, membership.trip_id, payment_id)
    if payment is None:
        raise PaymentNotFoundError(str(payment_id))
    await ensure_open(session, membership.trip_id)
    await _may_act_for(
        session, membership, payment.from_profile_id, payment.to_profile_id
    )
    await db.delete_payment(session, payment)
    await session.commit()


async def close(session: AsyncSession, membership: TripMembership) -> SettlementRead:
    """Close the settlement (host); new or changed expenses are then refused.

    Args:
        session: Open session.
        membership: The caller's membership, checked to be the host.

    Returns:
        The settlement with ``closed_at`` set (closing twice changes nothing).

    Raises:
        SettlementHasDraftsError: Draft expenses are still waiting.
    """
    await db.lock_settlement(session, membership.trip_id)
    if await db.select_closure(session, membership.trip_id) is None:
        drafts = await expense_db.count_drafts(session, membership.trip_id)
        if drafts:
            raise SettlementHasDraftsError(drafts)
        await db.insert_closure(
            session,
            TripSettlement(trip_id=membership.trip_id, closed_by_sub=membership.sub),
        )
        await session.commit()
    return await get_settlement(session, membership)


async def reopen(session: AsyncSession, membership: TripMembership) -> SettlementRead:
    """Reopen the settlement (host).

    Args:
        session: Open session.
        membership: The caller's membership, checked to be the host.

    Returns:
        The settlement with ``closed_at`` empty.
    """
    await db.lock_settlement(session, membership.trip_id)
    closure = await db.select_closure(session, membership.trip_id)
    if closure is not None:
        await db.delete_closure(session, closure)
        await session.commit()
    return await get_settlement(session, membership)
