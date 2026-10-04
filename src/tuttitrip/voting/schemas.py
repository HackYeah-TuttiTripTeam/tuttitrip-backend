"""Voting DTOs: links for people without an account and the aggregate result."""

from datetime import datetime
from enum import StrEnum, unique
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field

from tuttitrip.profiles.feedback.schemas import RatingValue, ReasonCode
from tuttitrip.shared.pagination.schemas import ListFilters, PageParams, SortDir
from tuttitrip.shared.permissions.schemas import AccessTokenState

VOTE_PATH = "/glos"
"""Frontend route of the voting page; the token goes after it in `#t=`."""


@unique
class VoteSource(StrEnum):
    """Where a vote came from."""

    APP = "app"
    LINK = "link"
    HOST = "host"


class VoteLinkCreate(BaseModel):
    """Payload for a new voting link."""

    profile_id: UUID = Field(
        description="Person without an account the link is for (on this trip)."
    )
    expires_in_days: int = Field(
        default=14, ge=1, le=90, description="Days until the link stops working."
    )


class VoteLinkRead(BaseModel):
    """A stored voting link (the token itself is never stored)."""

    id: UUID
    profile_id: UUID
    profile_name: str | None = Field(
        description="Display name of the person; null if the profile is gone."
    )
    state: AccessTokenState
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


class VoteLinkCreated(VoteLinkRead):
    """Response of creation: the only time the token is shown."""

    token: str = Field(
        description=(
            "The secret. Shown once and not recoverable. Never put it in a path "
            "or query: it only travels in the `#t=` fragment of `url` and, from "
            "the voting page, in the `X-Access-Token` header."
        )
    )
    url: str = Field(
        description=(
            f"`{VOTE_PATH}#t=<token>`: prefix it with the frontend origin to get "
            "the link (and the QR code). The fragment is never sent to a server."
        )
    )


@unique
class VoteSummarySort(StrEnum):
    """Sort keys of the vote summary."""

    NAME = "name"
    WANT = "want"
    DONT_WANT = "dont_want"
    VETO = "veto"


class VoteSummaryFilters(ListFilters):
    """Filters of the vote summary."""

    has_veto: bool | None = Field(
        default=None, description="Only places with (or without) an active veto."
    )
    source: VoteSource | None = Field(
        default=None, description="Only places with a vote or veto from this source."
    )


class VoteSummaryQuery(PageParams, VoteSummaryFilters):
    """Query of `GET /trips/{trip_id}/vote-summary`."""

    sort: VoteSummarySort = VoteSummarySort.VETO
    dir: SortDir = SortDir.DESC


class PersonVote(BaseModel):
    """One person's rating of a place."""

    profile_id: UUID
    display_name: str
    value: RatingValue
    reason_code: ReasonCode | None
    source: VoteSource
    updated_at: datetime


class PersonVeto(BaseModel):
    """One person's active veto of a place."""

    veto_id: UUID
    profile_id: UUID
    display_name: str
    source: VoteSource
    created_at: datetime


Count = Annotated[int, Field(ge=0)]


class PlaceVoteSummary(BaseModel):
    """What the group said about one place."""

    place_id: UUID
    place_name: str
    want: Count
    dont_want: Count
    neutral: Count
    veto_count: Count
    votes: list[PersonVote] = Field(description="Ratings with the people behind them.")
    vetoes: list[PersonVeto] = Field(
        description="Active vetoes with the people behind them."
    )
