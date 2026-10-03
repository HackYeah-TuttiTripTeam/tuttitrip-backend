"""Start plan generation in tuttitrip-worker (long-running, LLM)."""

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.schemas import PlanJobRequest
from tuttitrip.shared.jobs.contracts import (
    GenerateTripPlanInput,
    Workflow,
    queue_for,
)
from tuttitrip.shared.jobs.services.job_queue import JobQueue
from tuttitrip.shared.jobs.services.worker_liveness import ensure_worker_available
from tuttitrip.trips.schemas import TripRole
from tuttitrip.trips.services import trip_service


async def start_plan_generation(
    session: AsyncSession, queue: JobQueue, owner_sub: str, data: PlanJobRequest
) -> str:
    """Authorize, check the worker and enqueue ``generate_trip_plan``.

    Args:
        session: Open session.
        queue: Job queue.
        owner_sub: Auth0 subject of the caller.
        data: Trip and free-text request.

    Returns:
        The workflow id (the same one for a repeated identical request).
    """
    await trip_service.get_membership(
        session, data.trip_id, owner_sub, TripRole.CO_HOST
    )
    await ensure_worker_available(session)
    payload = GenerateTripPlanInput(
        trip_id=data.trip_id, request=data.request, provider=data.provider
    )
    return await queue.enqueue(
        Workflow.GENERATE_TRIP_PLAN,
        payload,
        user=owner_sub,
        key=str(data.trip_id),
        queue=queue_for(data.provider),
    )
