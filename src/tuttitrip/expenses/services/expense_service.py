"""Expenses of a trip: list, add, change and remove."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.expenses import db
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
    ExpenseCreate,
    ExpenseQuery,
    ExpenseRead,
    ExpenseUpdate,
    ParticipantRead,
    ShareInput,
)
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


def _read(expense: Expense) -> ExpenseRead:
    shares = [Share(s.profile_id, s.value) for s in expense.shares]
    cents = allocate(to_cents(expense.amount), expense.split_method, shares)
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
) -> str:
    """Check the expense against the trip.

    Returns:
        The currency the expense is stored in.

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
    return expense.currency or str(trip.currency)


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
    """
    currency = await _check(session, membership, data)
    expense = Expense(
        trip_id=membership.trip_id,
        payer_profile_id=data.payer_profile_id,
        amount=data.amount,
        currency=currency,
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
    changes = data.model_dump(exclude_unset=True, exclude={"participants"})
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
        }
    )
    await _check(session, membership, merged)
    for field, value in changes.items():
        setattr(expense, field, value)
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
    await db.delete_expense(session, expense)
    await session.commit()
