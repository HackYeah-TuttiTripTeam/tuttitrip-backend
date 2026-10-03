"""Profile DTOs."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgeGroup(StrEnum):
    """Age group that drives default constraints (distance, naps, pace)."""

    CHILD = "child"
    TEEN = "teen"
    ADULT = "adult"
    SENIOR = "senior"


class ProfileRead(BaseModel):
    """A person on a trip."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trip_id: UUID
    display_name: str
    age_group: AgeGroup
    weight: float = Field(description="Vote multiplier in the fairness solver.")
