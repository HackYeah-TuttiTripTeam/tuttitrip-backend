"""Expenses on a real PostgreSQL: constraints, filters, paging and cascades.

Needs a migrated database (``docker compose up -d --wait db && uv run alembic
upgrade head``); run with ``uv run pytest -m integration``.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tuttitrip.expenses.schemas import (
    ExpenseCategory,
    ExpenseCreate,
    ExpenseQuery,
    ExpenseRead,
    ExpenseSort,
    ExpenseUpdate,
    ShareInput,
    SplitMethod,
)
from tuttitrip.expenses.services import expense_service
from tuttitrip.profiles.schemas import ProfileCreate
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import database_url
from tuttitrip.shared.pagination.schemas import SortDir
from tuttitrip.trips.schemas import TripCreate, TripRole
from tuttitrip.trips.services import trip_service

pytestmark = pytest.mark.integration

HOST = "auth0|integration-host"
MEMBER = "auth0|integration-member"


@asynccontextmanager
async def _session() -> AsyncGenerator[AsyncSession]:
    engine = create_async_engine(database_url(get_settings().database))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


def _create(payer: UUID, *people: UUID, **over: object) -> ExpenseCreate:
    data: dict[str, object] = {
        "payer_profile_id": payer,
        "amount": Decimal("142.00"),
        "spent_on": date(2026, 11, 7),
        "participants": [ShareInput(profile_id=p) for p in people],
    }
    return ExpenseCreate.model_validate(data | over)


async def _scenario(session: AsyncSession) -> None:
    trip = await trip_service.create_trip(
        session, HOST, TripCreate(name="Integration", currency="PLN")
    )
    try:
        host_m = await trip_service.get_membership(
            session, trip.id, HOST, TripRole.HOST
        )
        kasia, ania, tomek = [
            (
                await profile_service.create_profile(
                    session, host_m, ProfileCreate(display_name=name, age=30)
                )
            ).id
            for name in ("Kasia", "Ania", "Tomek")
        ]

        dinner = await expense_service.create_expense(
            session,
            host_m,
            _create(kasia, kasia, tomek, category=ExpenseCategory.FOOD),
        )
        assert [p.amount for p in dinner.participants] == [Decimal(71)] * 2
        taxi = await expense_service.create_expense(
            session,
            host_m,
            _create(
                ania,
                kasia,
                ania,
                tomek,
                amount=Decimal("100.00"),
                spent_on=date(2026, 11, 9),
                split_method=SplitMethod.WEIGHTS,
                participants=[
                    ShareInput(profile_id=kasia, value=Decimal(1)),
                    ShareInput(profile_id=ania, value=Decimal(2)),
                    ShareInput(profile_id=tomek, value=Decimal(1)),
                ],
            ),
        )
        assert sorted(p.amount for p in taxi.participants) == [25, 25, 50]

        async def page(**query: object) -> list[ExpenseRead]:
            result = await expense_service.list_expenses(
                session, host_m, ExpenseQuery.model_validate(query)
            )
            return result.items

        assert [e.id for e in await page()] == [taxi.id, dinner.id]  # newest first
        assert [e.id for e in await page(dir=SortDir.ASC)] == [dinner.id, taxi.id]
        assert [e.id for e in await page(sort=ExpenseSort.AMOUNT)] == [
            dinner.id,
            taxi.id,
        ]
        assert [e.id for e in await page(date_from="2026-11-08")] == [taxi.id]
        assert [e.id for e in await page(date_to="2026-11-08")] == [dinner.id]
        assert [e.id for e in await page(payer_profile_id=ania)] == [taxi.id]
        assert [e.id for e in await page(participant_profile_id=ania)] == [taxi.id]
        assert len(await page(participant_profile_id=kasia)) == 2  # no repeats
        assert [e.id for e in await page(category="food")] == [dinner.id]
        second = await expense_service.list_expenses(
            session, host_m, ExpenseQuery(page=2, size=1)
        )
        assert (second.total, second.pages, len(second.items)) == (2, 2, 1)

        changed = await expense_service.update_expense(
            session,
            host_m,
            dinner.id,
            ExpenseUpdate(
                participants=[ShareInput(profile_id=ania), ShareInput(profile_id=tomek)]
            ),
        )
        assert {p.profile_id for p in changed.participants} == {ania, tomek}
        same_people = await expense_service.update_expense(
            session,
            host_m,
            dinner.id,
            ExpenseUpdate(
                participants=[ShareInput(profile_id=ania), ShareInput(profile_id=tomek)]
            ),
        )
        assert len(same_people.participants) == 2  # row switch, no duplicate key

        assert await expense_service.profile_in_use(session, host_m, ania)
        assert not await expense_service.profile_in_use(session, host_m, UUID(int=1))
        with pytest.raises(profile_service.ProfileInUseError):
            await profile_service.delete_profile(session, host_m, ania)

        await expense_service.delete_expense(session, host_m, taxi.id)
        await expense_service.delete_expense(session, host_m, dinner.id)
        await profile_service.delete_profile(session, host_m, ania)

        await expense_service.create_expense(
            session, host_m, _create(kasia, kasia, tomek)
        )
    finally:
        await session.rollback()
        # Deleting the trip must work although expenses reference its profiles.
        await trip_service.delete_trip(
            session,
            await trip_service.get_membership(session, trip.id, HOST, TripRole.HOST),
        )


def test_expense_lifecycle_on_postgres() -> None:
    async def run() -> None:
        async with _session() as session:
            await _scenario(session)

    asyncio.run(run())
