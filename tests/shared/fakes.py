"""In-memory test doubles."""

from tuttitrip.shared.jobs.contracts import WORKFLOWS, ContractPayload, Queue, Workflow
from tuttitrip.shared.jobs.schemas import JobState
from tuttitrip.shared.jobs.services.job_queue import (
    JobNotFoundError,
    workflow_id_for,
)


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
