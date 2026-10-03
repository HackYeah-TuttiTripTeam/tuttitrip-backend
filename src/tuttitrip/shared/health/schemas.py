"""Health check DTOs."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Overall status plus the database check."""

    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    environment: str


class LiveResponse(BaseModel):
    """Process liveness."""

    status: Literal["ok"] = "ok"
