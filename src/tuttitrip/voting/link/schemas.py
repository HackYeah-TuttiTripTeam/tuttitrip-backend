"""DTOs of voting through a link: what a person without an account sees and sends."""

from uuid import UUID

from pydantic import BaseModel, Field

from tuttitrip.profiles.feedback.schemas import RatingValue, ReasonCode


class VotePlace(BaseModel):
    """A place of the plan with this person's own answer; nothing about anybody else."""

    place_id: UUID
    name: str
    description: str | None = Field(
        description="Short text about the place; null: the catalog has none yet."
    )
    photo_url: str | None = Field(
        description="Photo of the place; null: the catalog has none yet."
    )
    category: str = Field(description="Catalog category of the place.")
    address: str | None = Field(description="Street address; null when unknown.")
    in_plan: bool = Field(
        description=(
            "Whether the place is in the current plan. A place the plan dropped "
            "(after a veto) still comes back, with this person's answer."
        )
    )
    rating: RatingValue | None = Field(description="This person's rating, if any.")
    reason_code: ReasonCode | None = Field(
        description="Why they are against it (only with `dont_want`)."
    )
    veto_id: UUID | None = Field(
        description="Set while this person's veto is active; use it to withdraw it."
    )


class VoteSession(BaseModel):
    """What the voting page shows: the trip, the person and the places to judge."""

    trip_name: str
    profile_name: str
    places: list[VotePlace] = Field(
        description=(
            "Places of the current plan in visiting order, then the places this "
            "person answered that the plan no longer has. A bounded document "
            "(one plan), not a paged list."
        )
    )


class VoteVetoCreate(BaseModel):
    """A veto of one place by the person the link belongs to."""

    place_id: UUID
