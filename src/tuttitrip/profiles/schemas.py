"""Profile DTOs."""

from datetime import time
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgeGroup(StrEnum):
    """Age group that drives default constraints (distance, naps, pace)."""

    CHILD = "child"
    TEEN = "teen"
    ADULT = "adult"
    SENIOR = "senior"


class WeightPreset(StrEnum):
    """Ready-made weight settings."""

    PO_ROWNO = "po_rowno"
    POD_DZIECI = "pod_dzieci"
    DZIEN_BABCI = "dzien_babci"


class _Comfort(BaseModel):
    """Comfort fields shared by create, update and read (section 2 of the spec)."""

    segment_km: float = Field(gt=0, le=50, description="Longest walk in one go.")
    daily_km: float = Field(gt=0, le=100, description="Daily walking distance.")
    active_min: int = Field(gt=0, le=1440, description="Active minutes per day.")
    stairs_sensitivity: float = Field(ge=0, le=1)
    queue_patience_min: int = Field(ge=0, le=600)
    nap_start: time | None = None
    nap_minutes: int = Field(ge=0, le=600)
    floor: int = Field(ge=0, le=100, description="Minimum welfare the person needs.")


class ProfileCreate(BaseModel):
    """A new person; every comfort field defaults from the age."""

    display_name: str = Field(min_length=1, max_length=100)
    age: int = Field(ge=0, le=120)
    user_sub: str | None = Field(
        default=None,
        max_length=255,
        description="Auth0 subject of a trip member this profile belongs to.",
    )
    segment_km: float | None = Field(default=None, gt=0, le=50)
    daily_km: float | None = Field(default=None, gt=0, le=100)
    active_min: int | None = Field(default=None, gt=0, le=1440)
    stairs_sensitivity: float | None = Field(default=None, ge=0, le=1)
    queue_patience_min: int | None = Field(default=None, ge=0, le=600)
    nap_start: time | None = None
    nap_minutes: int | None = Field(default=None, ge=0, le=600)
    floor: int | None = Field(default=None, ge=0, le=100)


class ProfileUpdate(BaseModel):
    """Partial update; omitted fields stay (or follow a new age group)."""

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    age: int | None = Field(default=None, ge=0, le=120)
    user_sub: str | None = Field(
        default=None, max_length=255, description="Co-host and above only."
    )
    segment_km: float | None = Field(default=None, gt=0, le=50)
    daily_km: float | None = Field(default=None, gt=0, le=100)
    active_min: int | None = Field(default=None, gt=0, le=1440)
    stairs_sensitivity: float | None = Field(default=None, ge=0, le=1)
    queue_patience_min: int | None = Field(default=None, ge=0, le=600)
    nap_start: time | None = None
    nap_minutes: int | None = Field(default=None, ge=0, le=600)
    floor: int | None = Field(default=None, ge=0, le=100)


class ProfileRead(_Comfort):
    """A person on a trip."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trip_id: UUID
    display_name: str
    age: int
    age_group: AgeGroup
    user_sub: str | None
    weight: float = Field(description="Vote multiplier in the fairness solver.")


class WeightItem(BaseModel):
    """One person's weight."""

    profile_id: UUID
    weight: float = Field(gt=0)


class WeightsUpdate(BaseModel):
    """Set weights by a preset or by hand (exactly one of the two)."""

    preset: WeightPreset | None = None
    focus_profile_id: UUID | None = Field(
        default=None, description="The chosen person for preset dzien_babci."
    )
    weights: list[WeightItem] | None = Field(
        default=None, description="Weights of some people; the rest keep theirs."
    )

    @model_validator(mode="after")
    def _one_of_preset_or_weights(self) -> Self:
        if (self.preset is None) == (self.weights is None):
            msg = "Give either preset or weights"
            raise ValueError(msg)
        return self
