"""Rating and veto DTOs.

A rating is the vote ``v_ip`` of ``docs/algorytm.md`` (E1: want +1, neutral 0,
do not want -1). A veto is a separate hard constraint (E0), never a soft vote.
The reason codes are the shared contract enum from the plan, not new strings.
"""

from datetime import datetime
from enum import StrEnum, unique
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tuttitrip.planning.plans.schemas import ReasonCode

__all__ = [
    "RatingRead",
    "RatingUpdate",
    "RatingValue",
    "ReasonCode",
    "TripFeedback",
    "VetoCreate",
    "VetoRead",
]


@unique
class RatingValue(StrEnum):
    """A person's vote on a place."""

    WANT = "want"
    DONT_WANT = "dont_want"
    NEUTRAL = "neutral"

    @property
    def vote(self) -> int:
        """The vote ``v_ip`` in {-1, 0, +1} fed to E1.

        Returns:
            +1 for want, -1 for do not want, 0 for neutral.
        """
        return {"want": 1, "dont_want": -1, "neutral": 0}[self.value]


class RatingUpdate(BaseModel):
    """Set the rating of one person for one place (upsert)."""

    value: RatingValue
    reason_code: ReasonCode | None = Field(
        default=None, description="Required for dont_want, forbidden otherwise."
    )

    @model_validator(mode="after")
    def _reason_only_for_dont_want(self) -> Self:
        if self.value is RatingValue.DONT_WANT and self.reason_code is None:
            msg = "reason_code is required for dont_want"
            raise ValueError(msg)
        if self.value is not RatingValue.DONT_WANT and self.reason_code is not None:
            msg = "reason_code is allowed only for dont_want"
            raise ValueError(msg)
        return self


class RatingRead(BaseModel):
    """A stored rating."""

    model_config = ConfigDict(from_attributes=True)

    trip_id: UUID
    profile_id: UUID
    place_id: UUID
    value: RatingValue
    reason_code: ReasonCode | None
    updated_by_sub: str
    updated_at: datetime

    @property
    def vote(self) -> int:
        """The vote in {-1, 0, +1}.

        Returns:
            The numeric vote of ``value``.
        """
        return self.value.vote


class VetoCreate(BaseModel):
    """A veto of one person on one place."""

    profile_id: UUID
    place_id: UUID


class VetoRead(BaseModel):
    """A veto with its author; ``on_behalf`` when someone else filed it."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trip_id: UUID
    profile_id: UUID
    place_id: UUID
    created_by_sub: str
    on_behalf: bool
    created_at: datetime
    revoked_at: datetime | None


class TripFeedback(BaseModel):
    """What planning reads: every rating and the vetoes still in force."""

    ratings: list[RatingRead]
    vetoes: list[VetoRead] = Field(description="Active vetoes only (E0).")
