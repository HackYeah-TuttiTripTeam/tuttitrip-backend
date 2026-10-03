"""Job DTOs."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from tuttitrip.shared.jobs.contracts import Progress


class JobAccepted(BaseModel):
    """Returned when a job is enqueued."""

    workflow_id: str


class JobState(BaseModel):
    """Status of a workflow as seen by the API.

    ``status`` is DBOS's: ENQUEUED, DELAYED, PENDING, SUCCESS, ERROR,
    CANCELLED or MAX_RECOVERY_ATTEMPTS_EXCEEDED. Contract-version rejections
    by the worker show up as ERROR with the worker's message in ``error``.
    """

    workflow_id: str
    workflow_name: str
    status: str
    owner: str | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    progress: Progress | None = None


class WorkerLiveness(BaseModel):
    """Worker heartbeat summary for /health and enqueue guards."""

    status: Literal["ok", "stale", "missing"]
    backend_contract_version: int
    worker_contract_version: int | None = None
    worker_min_contract_version: int | None = None
    worker_app_version: str | None = None
    last_seen: datetime | None = None
    compatible: bool | None = None
