"""In-memory test doubles."""

from collections.abc import Iterable

from fastapi import FastAPI

from tuttitrip.shared.auth.api import get_current_user
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.jobs.contracts import WORKFLOWS, ContractPayload, Queue, Workflow
from tuttitrip.shared.jobs.schemas import JobState
from tuttitrip.shared.jobs.services.job_queue import (
    JobNotFoundError,
    workflow_id_for,
)
from tuttitrip.shared.permissions.api import get_user_grants
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature


class FakeJobQueue:
    """Records enqueues; jobs stay ENQUEUED until a test changes them."""

    def __init__(self) -> None:
        self.jobs: dict[str, JobState] = {}
        self.payloads: dict[str, ContractPayload] = {}
        self.queues: dict[str, Queue] = {}

    async def enqueue(
        self,
        workflow: Workflow,
        payload: ContractPayload,
        *,
        user: str,
        key: str,
        queue: Queue | None = None,
    ) -> str:
        workflow_id = workflow_id_for(workflow, key, payload)
        self.jobs.setdefault(
            workflow_id,
            JobState(
                workflow_id=workflow_id,
                workflow_name=workflow.value,
                status="ENQUEUED",
                owner=user,
            ),
        )
        self.payloads[workflow_id] = payload
        self.queues[workflow_id] = queue or WORKFLOWS[workflow].queue
        return workflow_id

    async def get(self, workflow_id: str) -> JobState:
        try:
            return self.jobs[workflow_id]
        except KeyError as exc:
            raise JobNotFoundError(workflow_id) from exc

    async def cancel(self, workflow_id: str) -> None:
        job = await self.get(workflow_id)
        if job.status in {"ENQUEUED", "PENDING"}:
            self.jobs[workflow_id] = job.model_copy(update={"status": "CANCELLED"})


EVERYTHING = (Grant(Feature.ROOT, Access.WRITE),)


def authorize(
    app: FastAPI, user: AuthenticatedUser, grants: Iterable[Grant] = EVERYTHING
) -> None:
    """Skip token checks and the grants query: ``user`` holds exactly ``grants``.

    Tests that are not about permissions use the default (``*`` WRITE).
    """
    fixed = list(grants)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_user_grants] = lambda: fixed
