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

__all__ = [
    "LINK_AUTHOR_PREFIX",
    "RatingRead",
    "RatingUpdate",
    "RatingValue",
    "ReasonCode",
    "TripFeedback",
    "VetoCreate",
    "VetoRead",
    "VotingActor",
    "link_author",
]


LINK_AUTHOR_PREFIX = "link:"
"""Author marker of a rating or veto made through a voting link.

The author columns (`updated_by_sub`, `created_by_sub`) hold `link:<token id>`
instead of an Auth0 subject, so the vote summary can tell the source apart.
"""


def link_author(token_id: UUID) -> str:
    """The author value of a vote cast through a voting link.

    Args:
        token_id: Id of the access token (never the token itself).

    Returns:
        `link:<token id>`.
    """
    return f"{LINK_AUTHOR_PREFIX}{token_id}"


@unique
class ReasonCode(StrEnum):
    """Why a person is against a place; shared with the plan verdict reasons."""

    TOO_EXPENSIVE = "too_expensive"
    TOO_FAR = "too_far"
    NOT_MY_STYLE = "not_my_style"
    TOO_CROWDED = "too_crowded"
    TOO_HARD_FOR_CHILD = "too_hard_for_child"
    OTHER = "other"


@unique
class RatingValue(StrEnum):
    """A person's vote on a place."""

    WANT = "want"
    DONT_WANT = "dont_want"
    NEUTRAL = "neutral"


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
    revoked_by_sub: str | None


class TripFeedback(BaseModel):
    """What planning reads: every rating and the vetoes still in force."""

    ratings: list[RatingRead]
    vetoes: list[VetoRead] = Field(description="Active vetoes only (E0).")


class VotingActor(BaseModel):
    """Whose answers are written and who is recorded as the author.

    Built by a caller that has already checked the author may act for the
    profile (a voting token bound to it), so services need no ``CurrentUser``.
    """

    model_config = ConfigDict(frozen=True)

    trip_id: UUID
    profile_id: UUID
    author: str = Field(description="`link:<token id>` for a voting link.")
