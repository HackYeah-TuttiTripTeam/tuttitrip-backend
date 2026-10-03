"""Health check DTOs."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Overall status, the database check and the worker heartbeat.

    ``status`` is ``degraded`` when the database is down or the worker speaks
    an incompatible contract version. A stale or missing worker alone keeps
    ``ok`` (the API itself works), but enqueue endpoints refuse while missing.
    """

    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    environment: str
    worker: Literal["ok", "stale", "missing"]
    contract_version: int
    worker_contract_version: int | None = None


class LiveResponse(BaseModel):
    """Process liveness."""

    status: Literal["ok"] = "ok"
