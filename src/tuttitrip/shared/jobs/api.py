"""Job endpoints and the ``JobQueueDep`` dependency.

* ``GET /jobs/{id}`` and ``POST /jobs/{id}/cancel``: the caller's own jobs.
* ``POST /jobs/ping`` and ``GET /jobs/ping/{id}``: unauthenticated echo
  through the worker, used by the post-deploy smoke test.
"""

import secrets
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from tuttitrip.shared.auth.api import CurrentUser
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.jobs.contracts import PingInput, Workflow
from tuttitrip.shared.jobs.schemas import JobAccepted, JobState
from tuttitrip.shared.jobs.services.job_queue import (
    DbosJobQueue,
    JobNotFoundError,
    JobQueue,
    JobQueueUnavailableError,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])

SMOKE_USER = "smoke-test"
_NOT_FOUND = "Job not found"
_UNAVAILABLE = "Job queue unavailable"


@lru_cache(maxsize=1)
def get_job_queue() -> JobQueue:
    """Build the DBOS-backed queue from settings (lazy connection).

    Returns:
        The process-wide job queue.
    """
    settings = get_settings()
    return DbosJobQueue(
        system_database_url=settings.dbos_system_database_url(),
        application_name=settings.dbos.application_name,
        application_version=settings.dbos.application_version,
    )


JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]


async def _load(queue: JobQueue, workflow_id: str) -> JobState:
    try:
        return await queue.get(workflow_id)
    except JobNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND) from exc
    except JobQueueUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _UNAVAILABLE) from exc


@router.post("/ping", status_code=status.HTTP_202_ACCEPTED)
async def ping(queue: JobQueueDep) -> JobAccepted:
    """Enqueue an echo workflow (no auth, no LLM) for smoke tests.

    Args:
        queue: Job queue.

    Returns:
        The workflow id to poll at ``GET /jobs/ping/{id}``.
    """
    payload = PingInput(message=secrets.token_hex(8))
    try:
        workflow_id = await queue.enqueue(
            Workflow.PING, payload, user=SMOKE_USER, key="smoke"
        )
    except JobQueueUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _UNAVAILABLE) from exc
    return JobAccepted(workflow_id=workflow_id)


@router.get("/ping/{workflow_id}")
async def ping_status(workflow_id: str, queue: JobQueueDep) -> JobState:
    """State of a ping job (only ping jobs are visible here).

    Args:
        workflow_id: Id returned by ``POST /jobs/ping``.
        queue: Job queue.

    Returns:
        The job state.
    """
    job = await _load(queue, workflow_id)
    if job.workflow_name != Workflow.PING.value:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return job


@router.get("/{workflow_id}")
async def get_job(workflow_id: str, user: CurrentUser, queue: JobQueueDep) -> JobState:
    """Status, result, error and progress of one of the caller's jobs.

    Args:
        workflow_id: Id returned when the job was enqueued.
        user: The authenticated caller.
        queue: Job queue.

    Returns:
        The job state.
    """
    job = await _load(queue, workflow_id)
    if job.owner != user.sub:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return job


@router.post("/{workflow_id}/cancel")
async def cancel_job(
    workflow_id: str, user: CurrentUser, queue: JobQueueDep
) -> JobState:
    """Cancel one of the caller's jobs.

    Args:
        workflow_id: Job id.
        user: The authenticated caller.
        queue: Job queue.

    Returns:
        The job state after cancelling.
    """
    job = await _load(queue, workflow_id)
    if job.owner != user.sub:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    try:
        await queue.cancel(workflow_id)
    except JobQueueUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _UNAVAILABLE) from exc
    return await _load(queue, workflow_id)
