"""Search DTOs."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EmbeddingHit(BaseModel):
    """One search result."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_kind: str
    source_id: str
    content: str
