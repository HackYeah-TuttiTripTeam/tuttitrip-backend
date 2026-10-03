"""Linter DTOs."""

from decimal import Decimal

from pydantic import BaseModel, Field


class PlanItem(BaseModel):
    """One stop in a plan."""

    name: str = Field(min_length=1)
    cost: Decimal = Field(default=Decimal(0), ge=0)


class LintRequest(BaseModel):
    """A plan to check plus the group budget."""

    items: list[PlanItem]
    budget: Decimal = Field(ge=0)


class Violation(BaseModel):
    """A single broken rule."""

    rule: str
    message: str


class LintReport(BaseModel):
    """All violations found in a plan."""

    violations: list[Violation]
