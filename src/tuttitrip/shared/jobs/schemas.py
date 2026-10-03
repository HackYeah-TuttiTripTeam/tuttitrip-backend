"""Job DTOs."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from tuttitrip.shared.jobs.contracts import Progress


class JobAccepted(BaseModel):
    """Returned when a job is enqueued."""

    workflow_id: str


class JobState(BaseModel):
    """Status of a workflow as seen by the API.

    ``status`` is DBOS's: ENQUEUED, DELAYED, PENDING, SUCCESS, ERROR,
    CANCELLED or MAX_RECOVERY_ATTEMPTS_EXCEEDED. Contract-version rejections
    by the worker show up as ERROR with the worker's message in ``error``.
    ``error_code`` is the worker's machine code (``invalid_payload``,
    ``unsupported_contract_version`` or ``not_implemented``); a job that fails
    with ``not_implemented`` is not retried and ``error`` says it is not
    available yet.
    """

    workflow_id: str
    workflow_name: str
    status: str
    owner: str | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = Field(
        default=None,
        description=(
            "Machine code of a worker error: `unsupported_contract_version`, "
            "`invalid_payload` or `not_implemented` (not retried). Clients branch "
            "on this, never on the text of `error`."
        ),
    )
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
