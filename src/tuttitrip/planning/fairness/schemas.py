"""Fairness DTOs."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class PersonUtility(BaseModel):
    """One person's satisfaction with a plan and their vote weight."""

    utility: float = Field(ge=0, le=100, description="Satisfaction u_i, 0-100.")
    weight: float = Field(default=1.0, gt=0, description="Vote multiplier w_i.")


class FairnessRequest(BaseModel):
    """Utilities of every person for a single candidate plan."""

    people: list[PersonUtility] = Field(min_length=1)
    alpha: float | None = Field(
        default=None,
        ge=0,
        le=3,
        description="Fairness slider 0 to 3; omitted gives the weighted log (alpha 1).",
    )


class FairnessScore(BaseModel):
    """Objective value W of a plan: sum of w_i * phi_alpha(u_i)."""

    score: float


class ConflictCode(StrEnum):
    """What a soft constraint of E5 was missed."""

    FLOOR = "floor"
    OWN_PLACE = "own_place"
    TAG_MINIMUM = "tag_minimum"


class Conflict(BaseModel):
    """One missed soft constraint, always with its cause (section 10)."""

    person_id: UUID
    code: ConflictCode
    tag: str | None = Field(default=None, description="Tag of a missed minimum.")
    missing: float = Field(
        ge=0,
        description=(
            "Floor: relative shortfall (f - u) / f. Own place: days without one. "
            "Tag minimum: places short of k."
        ),
    )


class FairnessReport(BaseModel):
    """Soft violations of a plan: it is still a plan, the misses are reported."""

    floors_missed: list[UUID] = Field(description="People below their floor.")
    violation: float = Field(ge=0, description="V(P) of E5.")
    conflicts: list[Conflict]
