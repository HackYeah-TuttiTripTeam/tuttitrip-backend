"""Expenses of a trip: list, add, change and remove."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.expenses import db
from tuttitrip.expenses.logic.rates import PLN, convert_cents, cross_rate
from tuttitrip.expenses.logic.split import (
    CENTS,
    Share,
    Violation,
    allocate,
    check_expense,
    to_cents,
)
from tuttitrip.expenses.models import Expense, ExpenseShare
from tuttitrip.expenses.schemas import (
    ExchangeRateRead,
    ExpenseCreate,
    ExpenseErrorCode,
    ExpenseQuery,
    ExpenseRead,
    ExpenseUpdate,
    ParticipantRead,
    ShareInput,
)
from tuttitrip.expenses.services import nbp_client
from tuttitrip.expenses.settlement.services import settlement_service
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service


class ExpenseNotFoundError(Exception):
    """The expense is not on this trip."""


class ExpenseForbiddenError(Exception):
    """Only the author, a co-host or the host may change or delete an expense."""


class ExpenseInvalidError(Exception):
    """The expense breaks a rule (carries the violations)."""

    def __init__(self, violations: list[Violation]) -> None:
        """Keep the violations for the API to report.

        Args:
            violations: Broken rules, never empty.
        """
        super().__init__(f"{len(violations)} expense rule(s) broken")
        self.violations = violations


@dataclass(frozen=True, slots=True)
class Pricing:
    """An expense in the trip's currency and the rate that got it there."""

    trip_amount: Decimal
    rate: Decimal | None = None
    source: str | None = None
    table: str | None = None
    rate_date: date | None = None


def _read(expense: Expense) -> ExpenseRead:
    shares = [Share(s.profile_id, s.value) for s in expense.shares]
    cents = allocate(to_cents(expense.trip_amount), expense.split_method, shares)
    rate = (
        ExchangeRateRead(
            rate=expense.rate,
            source=expense.rate_source,  # ty: ignore[invalid-argument-type] CHECKed
            table_no=expense.rate_table,
            effective_date=expense.rate_date,
        )
        if expense.rate is not None
        else None
    )
    participants = [
        ParticipantRead(
            profile_id=s.profile_id,
            value=s.value,
            amount=Decimal(cents[s.profile_id]) / CENTS,
        )
        for s in expense.shares
    ]
    return ExpenseRead(
        id=expense.id,
        trip_id=expense.trip_id,
        payer_profile_id=expense.payer_profile_id,
        amount=expense.amount,
        currency=expense.currency,
        trip_amount=expense.trip_amount,
        exchange_rate=rate,
        description=expense.description,
        spent_on=expense.spent_on,
        category=expense.category,
        split_method=expense.split_method,
        participants=participants,
        created_by_sub=expense.created_by_sub,
        created_at=expense.created_at,
    )


def _rows(participants: list[ShareInput]) -> list[ExpenseShare]:
    return [ExpenseShare(profile_id=p.profile_id, value=p.value) for p in participants]


async def _check(
    session: AsyncSession, membership: TripMembership, expense: ExpenseCreate
) -> tuple[str, str | None]:
    """Check the expense against the trip.

    Returns:
        The currency the expense is stored in and the trip's currency.

    Raises:
        ExpenseInvalidError: A rule is broken.
    """
    trip = await trip_service.get_trip(session, membership)
    profiles = await profile_service.list_profiles(session, membership)
    violations = check_expense(
        amount=expense.amount,
        currency=expense.currency,
        # Stored expenses keep their currency if the trip's changes later: it is
        # compared again only when the edit touches the amount or the currency.
        trip_currency=trip.currency if check_currency else expense.currency,
        payer=expense.payer_profile_id,
        method=expense.split_method,
        shares=[Share(p.profile_id, p.value) for p in expense.participants],
        trip_profiles={p.id for p in profiles},
    )
    if violations:
        raise ExpenseInvalidError(violations)
    return expense.currency or str(trip.currency), trip.currency


def _rate_error(code: ExpenseErrorCode, message: str) -> ExpenseInvalidError:
    return ExpenseInvalidError([Violation(code, "manual_rate", message)])


async def price(  # ruff: ignore[too-many-arguments] one conversion, explicit inputs
    *,
    amount: Decimal,
    currency: str,
    trip_currency: str | None,
    spent_on: date,
    manual_rate: Decimal | None,
    stored: Expense | None = None,
) -> Pricing:
    """Convert to the trip's currency: manual rate, stored rate or NBP.

    A rate stored on the expense is reused unless the day or the currency
    changed or a manual rate is sent, so editing a description never moves it.

    Args:
        amount: The cost in ``currency``.
        currency: Currency of the expense.
        trip_currency: The trip's currency, if it has one.
        spent_on: The day of the expense.
        manual_rate: A rate the caller supplies, if any.
        stored: The expense being changed, if any.

    Returns:
        The trip-currency amount and the rate used.

    Raises:
        ExpenseInvalidError: NBP has no rate or does not answer.
    """
    if trip_currency in {None, currency}:
        return Pricing(amount)
    if (
        stored is not None
        and stored.rate is not None
        and manual_rate is None
        and (stored.currency, stored.spent_on) == (currency, spent_on)
    ):
        return Pricing(
            Decimal(convert_cents(to_cents(amount), stored.rate)) / CENTS,
            stored.rate,
            stored.rate_source,
            stored.rate_table,
            stored.rate_date,
        )
    if manual_rate is not None:
        rate, source, table, day = manual_rate, "manual", None, None
    else:
        try:
            client = nbp_client.get_client()
            foreign = await client.quote(currency, spent_on)
            base = await client.quote(trip_currency or PLN, spent_on)
        except nbp_client.RateNotFoundError as exc:
            raise _rate_error(
                ExpenseErrorCode.RATE_NOT_FOUND,
                "NBP publishes no rate for this currency; send manual_rate",
            ) from exc
        except nbp_client.RateUnavailableError as exc:
            raise _rate_error(
                ExpenseErrorCode.RATE_UNAVAILABLE,
                "The NBP rate is unavailable; send manual_rate",
            ) from exc
        rate = cross_rate(foreign.mid, base.mid)
        tables = [q.table_no for q in (foreign, base) if q.table_no]
        source, table, day = "nbp", ", ".join(tables) or None, foreign.effective_date
    cents = convert_cents(to_cents(amount), rate)
    return Pricing(Decimal(cents) / CENTS, rate, source, table, day)


async def list_expenses(
    session: AsyncSession, membership: TripMembership, query: ExpenseQuery
) -> Page[ExpenseRead]:
    """One page of the trip's expenses.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        query: Paging, sorting and filters.

    Returns:
        The page.
    """
    page = await db.select_page(session, membership.trip_id, query)
    return Page[ExpenseRead](
        items=[_read(expense) for expense in page.items],
        total=page.total,
        page=page.page,
        size=page.size,
        pages=page.pages,
    )


async def create_expense(
    session: AsyncSession, membership: TripMembership, data: ExpenseCreate
) -> ExpenseRead:
    """Add an expense; the caller becomes its author.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        data: The expense.

    Returns:
        The created expense.

    Raises:
        ExpenseInvalidError: A rule is broken.
        SettlementClosedError: The settlement is closed.
    """
    await settlement_service.ensure_open(session, membership.trip_id)
    currency, trip_currency = await _check(session, membership, data)
    pricing = await price(
        amount=data.amount,
        currency=currency,
        trip_currency=trip_currency,
        spent_on=data.spent_on,
        manual_rate=data.manual_rate,
    )
    expense = Expense(
        trip_id=membership.trip_id,
        payer_profile_id=data.payer_profile_id,
        amount=data.amount,
        currency=currency,
        trip_amount=pricing.trip_amount,
        rate=pricing.rate,
        rate_source=pricing.source,
        rate_table=pricing.table,
        rate_date=pricing.rate_date,
        description=data.description,
        spent_on=data.spent_on,
        category=data.category,
        split_method=data.split_method,
        created_by_sub=membership.sub,
    )
    expense.shares = _rows(data.participants)
    await db.insert_expense(session, expense)
    await session.commit()
    await session.refresh(expense)
    return _read(expense)


async def _get(
    session: AsyncSession, membership: TripMembership, expense_id: UUID
) -> Expense:
    expense = await db.select_expense(session, membership.trip_id, expense_id)
    if expense is None:
        raise ExpenseNotFoundError(str(expense_id))
    return expense


def _require_author_or_host(membership: TripMembership, expense: Expense) -> None:
    if expense.created_by_sub != membership.sub and not membership.role.satisfies(
        TripRole.CO_HOST
    ):
        msg = "Only the author, a co-host or the host can change this expense"
        raise ExpenseForbiddenError(msg)


async def update_expense(
    session: AsyncSession,
    membership: TripMembership,
    expense_id: UUID,
    data: ExpenseUpdate,
) -> ExpenseRead:
    """Change an expense (author, co-host or host); the merged result is checked.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        expense_id: Expense to change.
        data: Only the fields that change.

    Returns:
        The changed expense.

    Raises:
        ExpenseNotFoundError: The expense is not on this trip.
        ExpenseForbiddenError: The caller is not the author nor a co-host.
        ExpenseInvalidError: The merged expense breaks a rule.
    """
    expense = await _get(session, membership, expense_id)
    _require_author_or_host(membership, expense)
    await settlement_service.ensure_open(session, membership.trip_id)
    changes = data.model_dump(
        exclude_unset=True, exclude={"participants", "manual_rate"}
    )
    participants = (
        data.participants
        if data.participants is not None
        else [
            ShareInput(profile_id=s.profile_id, value=s.value) for s in expense.shares
        ]
    )
    merged = ExpenseCreate.model_validate(
        {
            "payer_profile_id": expense.payer_profile_id,
            "amount": expense.amount,
            "currency": expense.currency,
            "description": expense.description,
            "spent_on": expense.spent_on,
            "category": expense.category,
            "split_method": expense.split_method,
            **changes,
            "participants": participants,
            "manual_rate": data.manual_rate,
        }
    )
    currency, trip_currency = await _check(session, membership, merged)
    pricing = await price(
        amount=merged.amount,
        currency=currency,
        trip_currency=trip_currency,
        spent_on=merged.spent_on,
        manual_rate=data.manual_rate,
        stored=expense,
    )
    for field, value in changes.items():
        setattr(expense, field, value)
    expense.trip_amount = pricing.trip_amount
    expense.rate = pricing.rate
    expense.rate_source = pricing.source
    expense.rate_table = pricing.table
    expense.rate_date = pricing.rate_date
    if data.participants is not None:
        await db.replace_shares(session, expense, _rows(participants))
    await session.commit()
    await session.refresh(expense)
    return _read(expense)


async def delete_expense(
    session: AsyncSession, membership: TripMembership, expense_id: UUID
) -> None:
    """Delete an expense (author, co-host or host).

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        expense_id: Expense to delete.

    Raises:
        ExpenseNotFoundError: The expense is not on this trip.
        ExpenseForbiddenError: The caller is not the author nor a co-host.
    """
    expense = await _get(session, membership, expense_id)
    _require_author_or_host(membership, expense)
    await settlement_service.ensure_open(session, membership.trip_id)
    await db.delete_expense(session, expense)
    await session.commit()


async def profile_in_use(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> bool:
    """Tell whether a person paid or shares an expense (they cannot be removed).

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        profile_id: Profile to check.

    Returns:
        True when the profile is referenced by an expense of this trip.
    """
    return await db.profile_has_expenses(session, membership.trip_id, profile_id)
