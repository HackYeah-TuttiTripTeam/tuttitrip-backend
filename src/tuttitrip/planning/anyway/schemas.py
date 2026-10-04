"""DTOs of the "anyway" suggestions endpoints."""

from uuid import UUID

from pydantic import BaseModel, Field

from tuttitrip.planning.plans.schemas import AnywaySuggestion


class AnywayRead(BaseModel):
    """The suggestions of one plan version."""

    plan_id: UUID
    suggestions: list[AnywaySuggestion] = Field(
        description="At most one per day; rejected ones are left out."
    )
    justification_pending: bool = Field(
        description=(
            "True while the model's text is still being written; until then "
            "`justification` is the template."
        )
    )
