"""The sample trip: copied once per account, from the stored seed, in one transaction.

The copy goes through the services of the other domains (trip, people,
preferences, ratings, plan, expenses, notification), so every rule applies as
for a real trip. It calls no LLM: the plan comes from the solver, which gives
the same ``plan_hash`` for the same dates and catalog. The first trip list of
an account triggers it (``ensure_sample_trip``); the demo reset calls
``create_sample_trip`` directly. The mark in ``sample_trip_grants`` keeps a
deleted sample from coming back.
"""

import logging
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tuttitrip.demo import db
from tuttitrip.demo.logic.sample_trip import (
    ExpenseSeed,
    Locale,
    locale_from_header,
    sample_trip,
)
from tuttitrip.demo.services import demo_service
from tuttitrip.expenses.schemas import ExpenseCreate, ShareInput
from tuttitrip.expenses.services import expense_service
from tuttitrip.notifications.schemas import (
    NotificationAction,
    NotificationActionCode,
    NotificationType,
)
from tuttitrip.notifications.services import notification_service
from tuttitrip.places.services import place_service
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.config.settings import SampleTripSettings, get_settings
from tuttitrip.shared.db.session import get_engine
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

log = logging.getLogger(__name__)


async def _add_expense(
    session: AsyncSession,
    membership: TripMembership,
    profiles: dict[str, ProfileRead],
    seed: ExpenseSeed,
    start: date,
) -> None:
    await expense_service.create_expense(
        session,
        membership,
        ExpenseCreate(
            payer_profile_id=profiles[seed.payer].id,
            amount=seed.amount,
            description=seed.description,
            spent_on=start + timedelta(days=seed.day_offset),
            category=seed.category,
            participants=[
                ShareInput(profile_id=profiles[n].id) for n in seed.participants
            ],
        ),
    )


async def _has_catalog(session: AsyncSession, settings: SampleTripSettings) -> bool:
    places = await place_service.list_places(
        session, settings.city_slug, None, limit=1, offset=0
    )
    return bool(places)


async def create_sample_trip(
    session: AsyncSession,
    sub: str,
    locale: Locale = "pl",
    today: date | None = None,
    settings: SampleTripSettings | None = None,
) -> UUID | None:
    """Copy the sample trip for the account and mark it (the services commit).

    Call it on a session joined to an outer transaction (see ``_in_transaction``)
    to make the whole copy atomic.

    Args:
        session: Open session.
        sub: Auth0 subject of the host.
        locale: Language of the content.
        today: The day dates are counted from (tests); defaults to today.
        settings: Sample trip settings; defaults to the application's.

    Returns:
        The new trip's id, or None when the sample is switched off or the
        catalog has no places for its city yet.
    """
    settings = settings or get_settings().sample_trip
    if not settings.enabled or not await _has_catalog(session, settings):
        return None
    today = today or date.today()  # ruff: ignore[call-date-today]  # a calendar day, not an instant
    content = sample_trip(locale, settings.city_slug, today)
    trip_id = await demo_service.create_seed_trip(session, sub, content.trip, today)
    await trip_service.mark_sample(session, trip_id)
    await session.commit()  # the plan service rolls back what is still open
    membership = await trip_service.get_membership(session, trip_id, sub, TripRole.HOST)
    plan, _ = await plan_service.generate_plan(session, membership, None)
    profiles = {
        p.display_name: p
        for p in await profile_service.list_profiles(session, membership)
    }
    start = today + timedelta(days=content.trip.starts_in_days)
    for expense in content.expenses:
        await _add_expense(session, membership, profiles, expense, start)
    await notification_service.notify(
        session,
        recipients=[sub],
        type=NotificationType.PLAN_READY,
        trip_id=trip_id,
        params={"plan_id": str(plan.id)},
        actions=[
            NotificationAction(
                code=NotificationActionCode.OPEN_PLAN, params={"plan_id": str(plan.id)}
            )
        ],
    )
    await db.add_sample_grant(session, sub)
    await session.commit()
    log.info(
        "Sample trip created for an account: trip=%s plan=%s", trip_id, plan.plan_hash
    )
    return trip_id


async def _in_transaction(
    engine: AsyncEngine, sub: str, locale: Locale, today: date | None
) -> UUID | None:
    """Create the sample under the account's lock, in one transaction.

    Args:
        engine: Database engine.
        sub: Auth0 subject of the caller.
        locale: Language of the content.
        today: The day dates are counted from, or None for today.

    Returns:
        The new trip's id, or None when nothing was created.
    """
    async with engine.connect() as connection, connection.begin():
        session = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            await db.lock_sample_grant(session, sub)
            if await db.has_sample_grant(session, sub):
                return None  # a parallel request was first
            return await create_sample_trip(session, sub, locale, today)
        finally:
            await session.close()


async def ensure_sample_trip(
    session: AsyncSession,
    sub: str,
    accept_language: str | None = None,
    today: date | None = None,
) -> UUID | None:
    """Give the account its sample trip on the first call, never again.

    Cheap on every later call (one lookup by primary key). A failure is logged
    and rolls the copy back; it never breaks the caller's trip list, and the
    next list tries again because no mark was written.

    Args:
        session: The request's session (only for the lookup).
        sub: Auth0 subject of the caller.
        accept_language: The ``Accept-Language`` header, if any.
        today: The day dates are counted from (tests); defaults to today.

    Returns:
        The new trip's id, or None when nothing was created.
    """
    try:
        if not get_settings().sample_trip.enabled or await db.has_sample_grant(
            session, sub
        ):
            return None
        return await _in_transaction(
            get_engine(), sub, locale_from_header(accept_language), today
        )
    except Exception:  # the sample must never break the trip list
        log.exception("Sample trip not created")
        return None


async def erase_account(session: AsyncSession, sub: str) -> dict[str, int]:
    """Forget the account's mark, without committing (the trip goes with its host).

    Args:
        session: Open session (caller commits).
        sub: Auth0 subject of the deleted account.

    Returns:
        ``sample_trip_marks_removed``.
    """
    return {"sample_trip_marks_removed": await db.delete_sample_grant(session, sub)}
