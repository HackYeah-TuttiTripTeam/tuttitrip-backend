"""Plan proposal DTOs."""

from datetime import datetime
from enum import StrEnum, unique
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

REMARK_MAX_LENGTH = 1000

Remark = Annotated[str, Field(min_length=1, max_length=REMARK_MAX_LENGTH)]


@unique
class ProposalErrorCode(StrEnum):
    """Stable code of a proposal error, sent as ``detail.code``."""

    OUTDATED = "proposal.outdated"


@unique
class ProposalDecision(StrEnum):
    """What a member does with a proposal."""

    APPROVE = "approve"
    REJECT = "reject"
    COMMENT = "comment"


@unique
class ProposalStatus(StrEnum):
    """The proposal as a whole.

    ``outdated`` wins (the plan changed after it was sent), then ``approved``
    (every member with an account approved), then ``rejected`` (somebody
    rejects) and else ``pending``.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    OUTDATED = "outdated"


class ProposalCreate(BaseModel):
    """Which plan to send; the latest version by default."""

    plan_id: UUID | None = Field(
        default=None,
        description="A stored version; it must still be the latest one (else 409).",
    )


class ResponseCreate(BaseModel):
    """A member's answer."""

    decision: ProposalDecision
    remark: Remark | None = Field(
        default=None, description="Required for `comment`, optional otherwise."
    )

    @model_validator(mode="after")
    def _comment_has_text(self) -> Self:
        if self.decision is ProposalDecision.COMMENT and self.remark is None:
            msg = "A comment needs a remark"
            raise ValueError(msg)
        return self


class ResponseRead(BaseModel):
    """One member's answer with their name."""

    profile_id: UUID | None = Field(
        description="The member's profile; null when it was removed."
    )
    display_name: str
    decision: ProposalDecision
    remark: str | None
    responded_at: datetime
    is_me: bool


class ProfileWithoutAccount(BaseModel):
    """A person with no account: their opinion comes from a voting link."""

    profile_id: UUID
    display_name: str


class ProposalTally(BaseModel):
    """Counts over the members with an account."""

    members: int = Field(ge=1, description="Everybody with an account on the trip.")
    approvals: int = Field(ge=0)
    rejections: int = Field(ge=0)
    comments: int = Field(ge=0)
    waiting: int = Field(ge=0, description="Members who have not answered yet.")


class ProposalRead(BaseModel):
    """A proposal with the status, the counts and the answers."""

    id: UUID
    trip_id: UUID
    plan_id: UUID
    plan_hash: str
    plan_version: int
    sent_by_name: str
    sent_at: datetime
    status: ProposalStatus
    tally: ProposalTally
    responses: list[ResponseRead]
    profiles_without_account: list[ProfileWithoutAccount]


class OutdatedDetail(BaseModel):
    """Why a proposal cannot be answered; clients map by ``code``."""

    code: Literal[ProposalErrorCode.OUTDATED] = ProposalErrorCode.OUTDATED
    message: str = Field(description="For developers; clients map by code.")
    latest_plan_id: UUID = Field(description="The version the proposal is behind.")


class OutdatedError(BaseModel):
    """409 body: the proposal is about an older plan version."""

    detail: OutdatedDetail
