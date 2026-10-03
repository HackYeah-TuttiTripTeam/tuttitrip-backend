"""Accommodation DTOs."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tuttitrip.accommodation.logic.keys import RequirementKind, is_known_key


class RequirementStatus(StrEnum):
    """Whether an offer satisfies a requirement."""

    MET = "met"
    UNMET = "unmet"
    UNCONFIRMED = "unconfirmed"


class Requirement(BaseModel):
    """A must-have feature, e.g. ``pool``."""

    feature: str = Field(min_length=1)


class OfferFeatures(BaseModel):
    """What we know about an offer: confirmed present or confirmed absent."""

    present: set[str] = Field(default_factory=set)
    absent: set[str] = Field(default_factory=set)


class RequirementCheck(BaseModel):
    """Result for one requirement."""

    feature: str
    status: RequirementStatus


class RequirementItem(BaseModel):
    """One lodging requirement of a trip (a switch the host turned on).

    ``hard`` requirements multiply into the lodging score and ``S_h`` of E2
    (unmet means 0); soft ones are averaged. A requirement applies to the one
    lodging base for the whole trip (docs/algorytm.md, section 9).
    """

    model_config = ConfigDict(from_attributes=True)

    kind: RequirementKind
    key: str = Field(min_length=1, max_length=64)
    hard: bool
    max_distance_m: int | None = Field(
        default=None, gt=0, description="Required for `distance`, forbidden otherwise."
    )

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not is_known_key(self.kind, self.key):
            msg = f"Unknown {self.kind} key '{self.key}'"
            raise ValueError(msg)
        if (self.kind is RequirementKind.DISTANCE) != (self.max_distance_m is not None):
            msg = "max_distance_m is required for distance and only for distance"
            raise ValueError(msg)
        return self


class RequirementsWrite(BaseModel):
    """PUT payload: the whole set of requirements replaces the stored one."""

    requirements: list[RequirementItem] = Field(default_factory=list, max_length=60)

    @model_validator(mode="after")
    def _check(self) -> Self:
        pairs = [(item.kind, item.key) for item in self.requirements]
        if len(set(pairs)) != len(pairs):
            msg = "each (kind, key) may appear only once"
            raise ValueError(msg)
        return self


class RequirementsRead(RequirementsWrite):
    """The requirements of a trip with their version."""

    version: int = Field(
        description=(
            "Counter of changes. An offer check stores the version it used; a "
            "different current version makes that result stale."
        )
    )
