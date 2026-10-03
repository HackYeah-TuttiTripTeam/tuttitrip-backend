"""Planning DTOs."""

from uuid import UUID

from pydantic import BaseModel, Field


class TripPlan(BaseModel):
    """A short trip plan suggested to the user."""

    destination: str = Field(min_length=1, description="Destination city or region.")
    days: int = Field(ge=1, le=30, description="Trip length in days.")
    highlights: list[str] = Field(
        default_factory=list,
        description="Places or activities worth visiting.",
    )


class PlanJobRequest(BaseModel):
    """Ask the worker to draft a plan for one of the caller's trips."""

    trip_id: UUID
    request: str = Field(min_length=1, max_length=4000)
