"""DTOs of the trip linter."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from tuttitrip.planning.linter.schemas import MAX_DOCUMENT_CHARS, LintReport
from tuttitrip.shared.jobs.contracts import (
    MatchCandidate,
    ProviderName,
    UnreadItem,
)
from tuttitrip.shared.jobs.schemas import JobAccepted


class PasteState(StrEnum):
    """Where the check of a pasted plan stands."""

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class PasteCreate(BaseModel):
    """A plan pasted from another tool (stored as typed, deleted with the trip)."""

    text: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_DOCUMENT_CHARS
        ),
    ]
    provider: ProviderName = "openrouter"


class PasteAccepted(JobAccepted):
    """The text is stored and the parse is queued."""

    paste_id: UUID = Field(description="Poll `GET .../linter/pastes/{paste_id}`.")


class PasteItemRead(BaseModel):
    """One item read from the pasted text and the catalog place it was matched to."""

    index: int = Field(ge=0)
    day: int | None = Field(description="Day number in the text; null when absent.")
    place_name: str
    quote: str = Field(description="The verbatim words of the pasted text.")
    status: Literal["matched", "needs_confirmation", "unrecognized"] = Field(
        description=(
            "`matched` also after the host picked a candidate (`chosen_by_host`). "
            "Anything else counts as an unknown place in the report."
        )
    )
    place_id: UUID | None = Field(
        description="The place the rules were applied to; null when none."
    )
    suggested_place_id: UUID | None = Field(
        default=None,
        description="The worker's unconfirmed pick of a `needs_confirmation` item.",
    )
    chosen_by_host: bool = False
    candidates: list[MatchCandidate] = Field(default_factory=list)


class PasteCheckRead(BaseModel):
    """The check of a pasted plan: state, items and, when done, the report."""

    paste_id: UUID
    trip_id: UUID
    state: PasteState
    job_id: str
    error_code: str | None = Field(default=None, description="Worker code on failure.")
    violations: int | None = Field(
        description="Violations of all rules (`report.count`); null until done."
    )
    report: LintReport | None = Field(
        description="Every rule with its count, also zeros; null until done."
    )
    items: list[PasteItemRead] = Field(default_factory=list)
    unread: list[UnreadItem] = Field(
        default_factory=list, description="Text the parser could not turn into items."
    )


class ItemPick(BaseModel):
    """The host's choice for an item the worker was unsure about."""

    place_id: UUID = Field(
        description="One of the item's `candidates` (or its `suggested_place_id`)."
    )


class StoredReport(BaseModel):
    """What is kept in ``paste_checks.report``: the lint report and the item list."""

    lint: LintReport
    items: list[PasteItemRead]
