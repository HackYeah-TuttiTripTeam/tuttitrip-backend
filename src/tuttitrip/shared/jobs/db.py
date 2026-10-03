"""Queries on the worker-shared tables."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.shared.jobs.models import JobResult, WorkerHeartbeat


async def select_latest_heartbeat(
    session: AsyncSession, env: str
) -> WorkerHeartbeat | None:
    """Most recent heartbeat of any worker in this environment.

    Args:
        session: Open session.
        env: Environment name (``main``, ``develop`` or a branch slug).

    Returns:
        The newest heartbeat, or None if no worker ever reported.
    """
    return await session.scalar(
        select(WorkerHeartbeat)
        .where(WorkerHeartbeat.env == env)
        .order_by(WorkerHeartbeat.last_seen.desc())
        .limit(1)
    )


async def select_job_result(
    session: AsyncSession, workflow_id: str
) -> JobResult | None:
    """Persistent result row written by the worker, if any.

    Args:
        session: Open session.
        workflow_id: DBOS workflow id.

    Returns:
        The stored result, or None.
    """
    return await session.get(JobResult, workflow_id)
