"""Budget consent queries on PostgreSQL."""

from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.budget_approvals.models import BudgetApproval
from tuttitrip.planning.budget_approvals.schemas import (
    BudgetApprovalFilters,
    BudgetApprovalQuery,
    BudgetApprovalSort,
    BudgetApprovalStatus,
)
from tuttitrip.shared.db.pagination import ordering, paginate
from tuttitrip.shared.pagination.schemas import Page

ApprovalSelect = Select[BudgetApproval]
_SORT = {BudgetApprovalSort.CREATED_AT: BudgetApproval.created_at}


def scoped(trip_id: UUID) -> ApprovalSelect:
    """Every query starts here: the approvals of one trip.

    Args:
        trip_id: Trip id (the route already proved the caller may use it).

    Returns:
        A select of ``BudgetApproval``.
    """
    return select(BudgetApproval).where(BudgetApproval.trip_id == trip_id)


def apply_filters(
    stmt: ApprovalSelect, filters: BudgetApprovalFilters
) -> ApprovalSelect:
    """Add the filters to a select.

    Args:
        stmt: A select of ``BudgetApproval``.
        filters: The requested filters; unset ones add nothing.

    Returns:
        The narrowed select.
    """
    if filters.status is not None:
        stmt = stmt.where(BudgetApproval.status == filters.status.value)
    return stmt


async def select_page(
    session: AsyncSession, trip_id: UUID, query: BudgetApprovalQuery
) -> Page[BudgetApproval]:
    """One page of a trip's consent questions.

    Args:
        session: Open session.
        trip_id: Trip id.
        query: Page, sort and filters.

    Returns:
        The page and the total.
    """
    stmt = apply_filters(scoped(trip_id), query)
    return await paginate(
        session, stmt, query, ordering(_SORT, query.sort, BudgetApproval.id)
    )


async def select_one(
    session: AsyncSession, trip_id: UUID, approval_id: UUID
) -> BudgetApproval | None:
    """One consent question of a trip.

    Args:
        session: Open session.
        trip_id: Trip id (a question of another trip is not found).
        approval_id: Approval id.

    Returns:
        The row, or None.
    """
    return await session.scalar(scoped(trip_id).where(BudgetApproval.id == approval_id))


async def select_for_plan(
    session: AsyncSession, plan_id: UUID
) -> BudgetApproval | None:
    """The question asked for a ``P_flex`` version.

    Args:
        session: Open session.
        plan_id: The ``P_flex`` version id.

    Returns:
        The row, or None.
    """
    return await session.scalar(
        select(BudgetApproval).where(BudgetApproval.flex_plan_id == plan_id)
    )


async def supersede_pending(session: AsyncSession, trip_id: UUID) -> list[UUID]:
    """Mark the pending question of a trip superseded.

    Args:
        session: Open session (the caller commits).
        trip_id: Trip id.

    Returns:
        Ids of the questions that changed (at most one).
    """
    result = await session.execute(
        update(BudgetApproval)
        .where(
            BudgetApproval.trip_id == trip_id,
            BudgetApproval.status == BudgetApprovalStatus.PENDING.value,
        )
        .values(status=BudgetApprovalStatus.SUPERSEDED.value)
        .returning(BudgetApproval.id)
    )
    return [row[0] for row in result.all()]
