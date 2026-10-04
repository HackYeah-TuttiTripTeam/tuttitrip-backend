"""Override and decision-log queries on PostgreSQL."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.overrides.models import PlanDecision, TripOverride
from tuttitrip.planning.overrides.schemas import DecisionQuery, DecisionSort
from tuttitrip.shared.db.pagination import ordering, paginate
from tuttitrip.shared.pagination.schemas import Page

_SORT = {DecisionSort.CREATED_AT: PlanDecision.created_at}


async def select_active(session: AsyncSession, trip_id: UUID) -> Sequence[TripOverride]:
    """Decisions in force, in a stable order.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        Rows not revoked, by place id.
    """
    result = await session.scalars(
        select(TripOverride)
        .where(TripOverride.trip_id == trip_id, TripOverride.revoked_at.is_(None))
        .order_by(TripOverride.place_id)
    )
    return result.all()


async def select_override(
    session: AsyncSession, trip_id: UUID, override_id: UUID
) -> TripOverride | None:
    """One decision of a trip.

    Args:
        session: Open session.
        trip_id: Trip id (a decision of another trip is not found).
        override_id: Decision id.

    Returns:
        The row, or None.
    """
    return await session.scalar(
        select(TripOverride).where(
            TripOverride.trip_id == trip_id, TripOverride.id == override_id
        )
    )


async def select_decisions(
    session: AsyncSession, trip_id: UUID, query: DecisionQuery
) -> Page[PlanDecision]:
    """One page of the decision log.

    Args:
        session: Open session.
        trip_id: Trip id.
        query: Page, sort and filters.

    Returns:
        The page and the total.
    """
    stmt = select(PlanDecision).where(PlanDecision.trip_id == trip_id)
    if query.kind is not None:
        stmt = stmt.where(PlanDecision.kind == query.kind.value)
    order = ordering(_SORT, query.sort, PlanDecision.id)
    return await paginate(session, stmt, query, order)


def log_decision(session: AsyncSession, decision: PlanDecision) -> None:
    """Append an entry to the decision log (the caller commits).

    Args:
        session: Open session.
        decision: The entry.
    """
    session.add(decision)
