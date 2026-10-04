"""Invitation DTOs."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from tuttitrip.profiles.schemas import ClaimableProfile
from tuttitrip.trips.schemas import TripRole

DEFAULT_TTL_DAYS = 7
MAX_TTL_DAYS = 30
DEFAULT_MAX_USES = 10
MAX_MAX_USES = 100
MAX_ACTIVE_PER_TRIP = 20
MAX_USES_FIELD = "max_uses"
PLACEHOLDER_NAME = "Uczestnik"

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class InvitationCreate(BaseModel):
    """Payload for a new invitation link."""

    expires_in_days: int = Field(
        default=DEFAULT_TTL_DAYS,
        ge=1,
        le=MAX_TTL_DAYS,
        description="Days until the link stops working.",
    )
    max_uses: int = Field(
        default=DEFAULT_MAX_USES,
        ge=1,
        le=MAX_MAX_USES,
        description=(
            "How many people may join with this link. A named invitation "
            "(`profile_id`) always has exactly 1."
        ),
    )
    profile_id: UUID | None = Field(
        default=None,
        description=(
            "Makes a named invitation: the person who joins takes over this "
            "profile. It must be on the trip and have no account (404 / 409)."
        ),
    )

    @model_validator(mode="after")
    def _named_is_single_use(self) -> Self:
        if self.profile_id is not None and self.max_uses != 1:
            if MAX_USES_FIELD in self.model_fields_set:
                msg = "A named invitation works once: omit max_uses or send 1"
                raise ValueError(msg)
            self.max_uses = 1
        return self


class InvitationRead(BaseModel):
    """A stored invitation's public data (the token itself is never stored)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trip_id: UUID
    created_by_sub: str
    created_at: datetime
    expires_at: datetime
    max_uses: int
    uses: int
    revoked_at: datetime | None
    profile_id: UUID | None = Field(
        description="Profile a named invitation hands over; None for a general link."
    )


class InvitationCreated(InvitationRead):
    """Response of creation: the only time the token is shown."""

    token: str = Field(
        description=(
            "The secret. Shown once and not recoverable. The link is "
            "`https://<frontend>/join#t=<token>` (a fragment, never a path or "
            "query); the frontend sends the token in the body of "
            "`POST /invitations/preview` and `POST /invitations/accept`."
        )
    )


class InvitationToken(BaseModel):
    """Body that carries the token (it never goes in the path or query)."""

    token: str = Field(description="Token read from the `#t=` fragment of the link.")


class InvitationAccept(InvitationToken):
    """Body of accepting an invitation."""

    display_name: DisplayName | None = Field(
        default=None,
        max_length=100,
        description=(
            f"Name on the new profile; `{PLACEHOLDER_NAME}` when omitted. "
            "Not used when a profile is taken over."
        ),
    )
    profile_id: UUID | None = Field(
        default=None,
        description=(
            "Take over this profile (from the preview's `claimable_profiles`) "
            "instead of creating a new one: your account is linked to it in "
            "the same transaction as the membership. 404 when it is not on "
            "the trip, 409 when it has an account, was taken a moment ago, or "
            "the invitation is named for a different profile. Ignored when "
            "you already have a profile on the trip."
        ),
    )


class InvitationPreview(BaseModel):
    """What a person sees before joining."""

    trip_name: str
    destination: str | None
    already_member: bool = Field(description="The caller is on the trip already.")
    claimable_profiles: list[ClaimableProfile] = Field(
        description=(
            "People on the trip without an account that you may take over: id, "
            "name and age group only. For a named invitation only its profile. "
            "Empty when you are on the trip already."
        )
    )
    named_profile_id: UUID | None = Field(
        description=(
            "The profile this invitation is made for, or null for an open "
            "invitation. Set also when that profile is no longer free "
            "(`claimable_profiles` is then empty) and when you are on the "
            "trip already."
        )
    )


class JoinResult(BaseModel):
    """Result of accepting an invitation."""

    trip_id: UUID
    profile_id: UUID = Field(description="The caller's profile on the trip.")
    role: TripRole
    already_member: bool = Field(
        description="The caller was on the trip; nothing was created."
    )
    profile_claimed: bool = Field(
        default=False,
        description="An existing profile was taken over by this request.",
    )
