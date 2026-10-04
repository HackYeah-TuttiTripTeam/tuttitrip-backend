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
    ExpenseDayTotal,
    ExpenseErrorCode,
    ExpenseQuery,
    ExpenseRead,
    ExpenseStatus,
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


class ExpenseNotDraftError(Exception):
    """Only a draft can be confirmed."""


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

    trip_amount: Decimal | None
    rate: Decimal | None = None
    source: str | None = None
    table: str | None = None
    rate_date: date | None = None


def _read(expense: Expense) -> ExpenseRead:
    shares = [Share(s.profile_id, s.value) for s in expense.shares]
    counted = expense.trip_amount if expense.trip_amount is not None else expense.amount
    cents = allocate(to_cents(counted), expense.split_method, shares)
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
        status=expense.status,
        has_evidence=expense.evidence_id is not None,
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
        trip_currency=trip.currency,
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
    currency, trip_currency = await _check(session, membership, data)
    await session.commit()  # end the read transaction: no DB tx held during NBP
    pricing = await price(
        amount=data.amount,
        currency=currency,
        trip_currency=trip_currency,
        spent_on=data.spent_on,
        manual_rate=data.manual_rate,
    )
    await settlement_service.ensure_open(session, membership.trip_id)
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
        status=ExpenseStatus.CONFIRMED,
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
    """
    return await _update(session, membership, expense_id, data, confirm=False)


async def confirm_expense(
    session: AsyncSession,
    membership: TripMembership,
    expense_id: UUID,
    data: ExpenseUpdate,
) -> ExpenseRead:
    """Confirm a draft read from a receipt, with the person's corrections.

    The expense is priced again, becomes ``confirmed`` (enters the settlement)
    and its receipt image is deleted.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        expense_id: The draft.
        data: Corrected fields (may be empty).

    Returns:
        The confirmed expense.

    Raises:
        ExpenseNotDraftError: The expense is already confirmed.
    """
    return await _update(session, membership, expense_id, data, confirm=True)


async def _update(
    session: AsyncSession,
    membership: TripMembership,
    expense_id: UUID,
    data: ExpenseUpdate,
    *,
    confirm: bool,
) -> ExpenseRead:
    """Apply an update; with ``confirm`` also reprice, confirm and drop the image.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        expense_id: Expense to change.
        data: Only the fields that change.
        confirm: Also confirm the draft and delete its image.

    Returns:
        The changed expense.

    Raises:
        ExpenseNotFoundError: The expense is not on this trip.
        ExpenseForbiddenError: The caller is not the author nor a co-host.
        ExpenseNotDraftError: ``confirm`` on an expense that is not a draft.
        ExpenseInvalidError: The merged expense breaks a rule.
    """
    expense = await _get(session, membership, expense_id)
    _require_author_or_host(membership, expense)
    if confirm and expense.status != ExpenseStatus.DRAFT:
        msg = "Only a draft can be confirmed"
        raise ExpenseNotDraftError(msg)
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
    await session.commit()  # end the read transaction: no DB tx held during NBP
    # A description-only edit never reprices (the trip's currency may have changed).
    pricing: Pricing | None = None
    if (
        confirm
        or data.manual_rate is not None
        or changes.keys()
        & {
            "amount",
            "currency",
            "spent_on",
        }
    ):
        pricing = await price(
            amount=merged.amount,
            currency=currency,
            trip_currency=trip_currency,
            spent_on=merged.spent_on,
            manual_rate=data.manual_rate,
            stored=expense,
        )
    await settlement_service.ensure_open(session, membership.trip_id)
    if pricing is not None:
        expense.trip_amount = pricing.trip_amount
        expense.rate = pricing.rate
        expense.rate_source = pricing.source
        expense.rate_table = pricing.table
        expense.rate_date = pricing.rate_date
    for field, value in changes.items():
        setattr(expense, field, value)
    if data.participants is not None:
        await db.replace_shares(session, expense, _rows(participants))
    if confirm:
        expense.status = ExpenseStatus.CONFIRMED
        evidence_id, expense.evidence_id = expense.evidence_id, None
        await session.flush()
        if evidence_id is not None:
            await db.delete_evidence(session, evidence_id)
    await session.commit()
    await session.refresh(expense)
    return _read(expense)


async def create_draft(
    session: AsyncSession,
    membership: TripMembership,
    data: ExpenseCreate,
    evidence_id: UUID,
) -> ExpenseRead:
    """Store an expense read from a receipt as a draft (no settlement effect).

    A rate that cannot be fetched now leaves ``trip_amount`` empty; confirming
    prices it again.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        data: The fields read.
        evidence_id: The stored image it comes from.

    Returns:
        The draft.

    Raises:
        ExpenseInvalidError: The fields break a rule.
    """
    currency, trip_currency = await _check(session, membership, data)
    await session.commit()  # end the read transaction: no DB tx held during NBP
    try:
        pricing = await price(
            amount=data.amount,
            currency=currency,
            trip_currency=trip_currency,
            spent_on=data.spent_on,
            manual_rate=None,
        )
    except ExpenseInvalidError:
        pricing = Pricing(None)  # unpriced draft: confirming fetches the rate
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
        status=ExpenseStatus.DRAFT,
        evidence_id=evidence_id,
    )
    expense.shares = _rows(data.participants)
    await db.insert_expense(session, expense)
    await session.commit()
    await session.refresh(expense)
    return _read(expense)


def read(expense: Expense) -> ExpenseRead:
    """Public view of one stored expense.

    Args:
        expense: A stored expense with its shares.

    Returns:
        The API shape.
    """
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
    evidence_id = expense.evidence_id
    await db.delete_expense(session, expense)
    if evidence_id is not None:
        await db.delete_evidence(session, evidence_id)
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


async def day_totals(
    session: AsyncSession, membership: TripMembership
) -> list[ExpenseDayTotal]:
    """What the trip spent per day and category (for the budget of each day).

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.

    Returns:
        One row per day and category, oldest day first.
    """
    return await db.select_day_totals(session, membership.trip_id)
