"""Settlement on a real PostgreSQL: payments, closing and trip deletion.

Needs a migrated database; run with ``uv run pytest -m integration``.
"""

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.domains.test_expenses_integration import HOST, _create, _session
from tuttitrip.expenses.services import expense_service
from tuttitrip.expenses.settlement.schemas import PaymentCreate, PaymentQuery
from tuttitrip.expenses.settlement.services import settlement_service
from tuttitrip.profiles.schemas import ProfileCreate
from tuttitrip.profiles.services import profile_service
from tuttitrip.trips.schemas import TripCreate, TripRole
from tuttitrip.trips.services import trip_service

pytestmark = pytest.mark.integration


async def _scenario(session: AsyncSession) -> None:
    trip = await trip_service.create_trip(
        session, HOST, TripCreate(name="Settlement", currency="PLN")
    )
    try:
        host = await trip_service.get_membership(session, trip.id, HOST, TripRole.HOST)
        a, b, c = [
            (
                await profile_service.create_profile(
                    session, host, ProfileCreate(display_name=name, age=30)
                )
            ).id
            for name in ("A", "B", "C")
        ]
        await expense_service.create_expense(
            session, host, _create(a, a, b, c, amount=Decimal("99.00"))
        )
        before = await settlement_service.get_settlement(session, host)
        assert sum(x.amount for x in before.balances) == 0
        assert sorted(t.amount for t in before.transfers) == [
            Decimal("33.00"),
            Decimal("33.00"),
        ]

        await settlement_service.mark_paid(
            session,
            host,
            PaymentCreate(from_profile_id=b, to_profile_id=a, amount=Decimal("33.00")),
        )
        after = await settlement_service.get_settlement(session, host)
        assert [(t.from_profile_id, t.to_profile_id) for t in after.transfers] == [
            (c, a)
        ]
        payments = await settlement_service.list_payments(session, host, PaymentQuery())
        assert payments.total == 1
        assert payments.items[0].paid_on == date.today()  # ruff: ignore[call-date-today] (same day)

        closed = await settlement_service.close(session, host)
        assert closed.closed_at is not None
        with pytest.raises(settlement_service.SettlementClosedError):
            await expense_service.create_expense(
                session, host, _create(a, a, b, c, amount=Decimal("10.00"))
            )
        reopened = await settlement_service.reopen(session, host)
        assert reopened.closed_at is None
    finally:
        await session.rollback()
        # Payments and the closing record go with the trip.
        await trip_service.delete_trip(
            session,
            await trip_service.get_membership(session, trip.id, HOST, TripRole.HOST),
        )


def test_settlement_lifecycle_on_postgres() -> None:
    async def run() -> None:
        async with _session() as session:
            await _scenario(session)

    asyncio.run(run())
