"""Trip DTOs."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TripCreate(BaseModel):
    """Payload for creating a trip."""

    name: str = Field(min_length=1, max_length=200)
    destination: str | None = Field(default=None, max_length=200)


class TripRead(BaseModel):
    """A trip as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    destination: str | None
    created_at: datetime
