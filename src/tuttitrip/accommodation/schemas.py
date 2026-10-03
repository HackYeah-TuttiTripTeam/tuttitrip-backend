"""Accommodation DTOs."""

from enum import StrEnum

from pydantic import BaseModel, Field


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
