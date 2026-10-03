"""Linter DTOs."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


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


MAX_DOCUMENT_CHARS = 20_000


class DocumentKind(StrEnum):
    """What a pasted text is: a plan from another tool or a lodging offer."""

    PLAN = "plan"
    OFFER = "offer"


class DocumentCreate(BaseModel):
    """Text pasted by the host (stored as typed, deleted with the trip)."""

    kind: DocumentKind
    text: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_DOCUMENT_CHARS
        ),
    ]


class DocumentRead(BaseModel):
    """A stored pasted text (not echoed back; the worker reads it by ``id``)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trip_id: UUID
    kind: DocumentKind
    created_by: str
    created_at: datetime
