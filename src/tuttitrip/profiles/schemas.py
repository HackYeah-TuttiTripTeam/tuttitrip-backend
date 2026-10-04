"""Profile DTOs."""

from datetime import time
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgeGroup(StrEnum):
    """Age group that drives default constraints (distance, naps, pace)."""

    TODDLER = "toddler"
    CHILD = "child"
    TEEN = "teen"
    ADULT = "adult"
    SENIOR = "senior"


class ClaimableProfile(BaseModel):
    """A person without an account that an invited account can take over.

    Deliberately minimal: shown to anyone holding a working invitation token.
    """

    profile_id: UUID
    display_name: str
    age_group: AgeGroup


class ProfileWeightPreset(StrEnum):
    """Ready-made weight settings."""

    PO_ROWNO = "po_rowno"
    POD_DZIECI = "pod_dzieci"
    DZIEN_BABCI = "dzien_babci"


SegmentKm = Annotated[float, Field(gt=0, le=50, description="Longest walk in one go.")]
DailyKm = Annotated[float, Field(gt=0, le=100, description="Daily walking distance.")]
ActiveMin = Annotated[int, Field(gt=0, le=1440, description="Active minutes per day.")]
StairsSensitivity = Annotated[float, Field(ge=0, le=1)]
QueuePatienceMin = Annotated[int, Field(ge=0, le=600)]
NapMinutes = Annotated[int, Field(ge=0, le=600)]
Floor = Annotated[
    int, Field(ge=0, le=100, description="Minimum welfare the person needs.")
]


def comfort_problem(
    segment_km: float, daily_km: float, nap_start: time | None, nap_minutes: int
) -> str | None:
    """Find a contradiction between comfort fields.

    Args:
        segment_km: Longest walk in one go.
        daily_km: Daily walking distance.
        nap_start: When the nap starts, if any.
        nap_minutes: Nap length.

    Returns:
        A description of the problem, or None when the fields agree.
    """
    if segment_km > daily_km:
        return "segment_km must not exceed daily_km"
    if (nap_minutes > 0) != (nap_start is not None):
        return "nap_start and nap_minutes > 0 must be given together"
    return None


class _Comfort(BaseModel):
    """Comfort fields of one person (section 2 of the spec)."""

    segment_km: SegmentKm
    daily_km: DailyKm
    active_min: ActiveMin
    stairs_sensitivity: StairsSensitivity
    queue_patience_min: QueuePatienceMin
    nap_start: time | None = None
    nap_minutes: NapMinutes
    floor: Floor


class _ComfortOverrides(BaseModel):
    """Optional comfort fields; omitted ones come from the age group."""

    user_sub: str | None = Field(
        default=None,
        max_length=255,
        description="Auth0 subject of a trip member this profile belongs to.",
    )
    segment_km: SegmentKm | None = None
    daily_km: DailyKm | None = None
    active_min: ActiveMin | None = None
    stairs_sensitivity: StairsSensitivity | None = None
    queue_patience_min: QueuePatienceMin | None = None
    nap_start: time | None = None
    nap_minutes: NapMinutes | None = None
    floor: Floor | None = None

    @model_validator(mode="after")
    def _fields_agree(self) -> Self:
        # Only what the payload fixes itself; the service checks the merged result.
        if self.segment_km is not None and self.daily_km is not None:
            problem = comfort_problem(self.segment_km, self.daily_km, None, 0)
            if problem is not None:
                raise ValueError(problem)
        if {"nap_start", "nap_minutes"} <= self.model_fields_set:
            problem = comfort_problem(1, 1, self.nap_start, self.nap_minutes or 0)
            if problem is not None:
                raise ValueError(problem)
        return self


class ProfileCreate(_ComfortOverrides):
    """A new person; every comfort field defaults from the age."""

    display_name: str = Field(min_length=1, max_length=100)
    age: int = Field(ge=0, le=120)


class ProfileUpdate(_ComfortOverrides):
    """Partial update; omitted fields stay (or follow a new age group).

    ``user_sub`` is for co-hosts and above.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    age: int | None = Field(default=None, ge=0, le=120)


class ProfileRead(_Comfort):
    """A person on a trip."""

    # Filled by the service on every read, so it is always present in responses.
    model_config = ConfigDict(
        from_attributes=True, json_schema_serialization_defaults_required=True
    )

    id: UUID
    trip_id: UUID
    display_name: str
    age: int
    age_group: AgeGroup
    user_sub: str | None
    weight: float = Field(description="Vote multiplier in the fairness solver.")
    customized_fields: list[str] = Field(
        default_factory=list,
        json_schema_extra={"readOnly": True},
        description=(
            "Comfort fields that differ from the defaults of the person's age "
            "group, in schema order. Read-only, computed on read."
        ),
    )


class WeightItem(BaseModel):
    """One person's weight."""

    profile_id: UUID
    # Unconstrained on purpose: a NaN input would crash FastAPI's 422 body.
    # Positive, finite and the spread are checked in validate_weights.
    weight: float


class WeightsUpdate(BaseModel):
    """Set weights by a preset or by hand (exactly one of the two)."""

    preset: ProfileWeightPreset | None = None
    focus_profile_id: UUID | None = Field(
        default=None, description="The chosen person for preset dzien_babci."
    )
    weights: list[WeightItem] | None = Field(
        default=None,
        min_length=1,
        description="Weights of some people (each once); the rest keep theirs.",
    )

    @model_validator(mode="after")
    def _no_duplicate_profiles(self) -> Self:
        ids = [item.profile_id for item in self.weights or []]
        if len(ids) != len(set(ids)):
            msg = "Each profile_id may appear only once in weights"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _one_of_preset_or_weights(self) -> Self:
        if (self.preset is None) == (self.weights is None):
            msg = "Give either preset or weights"
            raise ValueError(msg)
        return self


class AccessTokenCreate(BaseModel):
    """Payload for a token that lets the profile's person act without an account."""

    expires_in_days: int = Field(
        default=14, ge=1, le=90, description="Days until the link stops working."
    )
