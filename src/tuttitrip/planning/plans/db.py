"""Plan version queries on PostgreSQL."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.plans.models import PlanVersion


async def lock_trip_plans(session: AsyncSession, trip_id: UUID) -> None:
    """Serialise plan writes of one trip until the transaction ends.

    Args:
        session: Open session (the lock is released at commit or rollback).
        trip_id: Trip id.
    """
    # The "plans:" prefix keeps the key apart from other users of advisory locks.
    key = func.hashtextextended(f"plans:{trip_id}", 0)
    await session.execute(select(func.pg_advisory_xact_lock(key)))


async def select_latest(session: AsyncSession, trip_id: UUID) -> PlanVersion | None:
    """Newest version of a trip.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The version, or None when the trip has no plan.
    """
    stmt = (
        select(PlanVersion)
        .where(PlanVersion.trip_id == trip_id, PlanVersion.alternative_of.is_(None))
        .order_by(PlanVersion.version.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def select_by_id(
    session: AsyncSession, trip_id: UUID, plan_id: UUID
) -> PlanVersion | None:
    """One version of a trip.

    Args:
        session: Open session.
        trip_id: Trip id (a plan of another trip is not found).
        plan_id: Version id.

    Returns:
        The version, or None.
    """
    stmt = select(PlanVersion).where(
        PlanVersion.trip_id == trip_id, PlanVersion.id == plan_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def select_proposal(
    session: AsyncSession, trip_id: UUID, plan_id: UUID, digest: str | None = None
) -> PlanVersion | None:
    """The newest budget proposal that is an alternative of a plan version.

    A proposal is a stored alternative whose ``params`` carry a ``proposal``
    entry; the E6 ``P_strict`` alternatives do not.

    Args:
        session: Open session.
        trip_id: Trip id.
        plan_id: The plan version the proposal is an alternative of.
        digest: Input hash to match, or None for any (the newest).

    Returns:
        The proposal, or None.
    """
    stmt = select(PlanVersion).where(
        PlanVersion.trip_id == trip_id,
        PlanVersion.alternative_of == plan_id,
        PlanVersion.params.has_key("proposal"),
    )
    if digest is not None:
        stmt = stmt.where(PlanVersion.input_hash == digest)
    stmt = stmt.order_by(PlanVersion.created_at.desc(), PlanVersion.id).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()
