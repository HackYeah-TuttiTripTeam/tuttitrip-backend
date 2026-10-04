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


async def select_by_input_hash(
    session: AsyncSession, trip_id: UUID, input_hash: str
) -> PlanVersion | None:
    """Find the newest version computed from the same input.

    Args:
        session: Open session.
        trip_id: Trip id.
        input_hash: Hash of the planning input.

    Returns:
        The version, or None.
    """
    stmt = (
        select(PlanVersion)
        .where(PlanVersion.trip_id == trip_id, PlanVersion.input_hash == input_hash)
        .order_by(PlanVersion.version.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


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
        .where(PlanVersion.trip_id == trip_id)
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


async def next_version(session: AsyncSession, trip_id: UUID) -> int:
    """The number the next version of the trip gets.

    Args:
        session: Open session (call under ``lock_trip_plans``).
        trip_id: Trip id.

    Returns:
        Highest stored version plus one, 1 for a trip without plans.
    """
    stmt = select(func.coalesce(func.max(PlanVersion.version), 0)).where(
        PlanVersion.trip_id == trip_id
    )
    return (await session.execute(stmt)).scalar_one() + 1
