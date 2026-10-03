"""Invitation DTOs."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tuttitrip.trips.schemas import TripRole

DEFAULT_TTL_DAYS = 7
MAX_TTL_DAYS = 30
DEFAULT_MAX_USES = 10
MAX_MAX_USES = 100
MAX_ACTIVE_PER_TRIP = 20
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
        description="How many people may join with this link.",
    )


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
        description=f"Name on the new profile; `{PLACEHOLDER_NAME}` when omitted.",
    )


class InvitationPreview(BaseModel):
    """What a person sees before joining."""

    trip_name: str
    destination: str | None
    already_member: bool = Field(description="The caller is on the trip already.")


class JoinResult(BaseModel):
    """Result of accepting an invitation."""

    trip_id: UUID
    profile_id: UUID = Field(description="The caller's profile on the trip.")
    role: TripRole
    already_member: bool = Field(
        description="The caller was on the trip; nothing was created."
    )
