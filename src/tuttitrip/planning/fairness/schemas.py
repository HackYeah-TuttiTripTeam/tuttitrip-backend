"""Fairness DTOs."""

from pydantic import BaseModel, Field


class PersonUtility(BaseModel):
    """One person's satisfaction with a plan and their vote weight."""

    utility: float = Field(ge=0, le=100, description="Satisfaction u_i, 0-100.")
    weight: float = Field(default=1.0, gt=0, description="Vote multiplier w_i.")


class FairnessRequest(BaseModel):
    """Utilities of every person for a single candidate plan."""

    people: list[PersonUtility] = Field(min_length=1)


class FairnessScore(BaseModel):
    """Objective value of a plan: sum of w_i * log(1 + u_i)."""

    score: float
