"""Override and decision-log DTOs."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tuttitrip.planning.plans.schemas import PlanConflict
from tuttitrip.shared.pagination.schemas import ListFilters, PageParams, SortDir


@unique
class OverrideKind(StrEnum):
    """What the host does with a place; both are hard constraints (E0)."""

    MUST = "must"
    BLOCK = "block"


@unique
class DecisionKind(StrEnum):
    """What a log entry records."""

    MUST = "must"
    BLOCK = "block"
    REVOKE = "revoke"
    BUDGET_APPROVAL = "budget_approval"


Reason = Annotated[str, Field(min_length=1, max_length=500)]


class OverrideCreate(BaseModel):
    """Force a place into the plan or block it."""

    place_id: UUID
    kind: OverrideKind
    reason: Reason | None = None


class PersonDelta(BaseModel):
    """Change of one person's ``r`` caused by a decision."""

    profile_id: UUID
    d_r: float


class DecisionEffects(BaseModel):
    """What a decision costs: the plan with it minus the plan without it.

    Fairness is the measure of the ledger (``min r`` and Jain's index of ``r``,
    docs/algorytm.md sections 7 and 10). The solo plans are not recomputed (the
    reference points ``u*`` of the plan without the decision are reused), so a
    block that a person wanted is charged to them through ``r``.
    """

    d_min_r: float
    d_jain: float
    d_r: list[PersonDelta]
    d_cost: Decimal = Field(description="Change of c(P), in the trip currency.")
    d_minutes: int = Field(description="Change of the active minutes of the plan.")


class OverridePreview(BaseModel):
    """The cost of a decision, nothing stored."""

    kind: OverrideKind
    place_id: UUID
    effects: DecisionEffects


class OverrideRead(BaseModel):
    """A decision of the host with the effects it had when it was made."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trip_id: UUID
    place_id: UUID
    kind: OverrideKind
    reason: str | None
    created_by_sub: str
    created_at: datetime
    revoked_at: datetime | None
    effects: DecisionEffects | None = Field(
        default=None, description="Same numbers as the log entry."
    )


class OverrideConflict(BaseModel):
    """409: the decision contradicts a hard constraint."""

    detail: str
    conflicts: list[PlanConflict]


class DecisionRead(BaseModel):
    """An entry of the append-only log."""

    id: UUID
    trip_id: UUID
    kind: DecisionKind
    place_id: UUID | None
    reason: str | None
    effects: DecisionEffects
    created_by_sub: str
    created_at: datetime


@unique
class DecisionSort(StrEnum):
    """Sort keys of the decision log."""

    CREATED_AT = "created_at"


class DecisionFilters(ListFilters):
    """Filters of the decision log."""

    kind: DecisionKind | None = None


class DecisionQuery(PageParams, DecisionFilters):
    """Query of ``GET .../decisions``."""

    sort: DecisionSort = DecisionSort.CREATED_AT
    dir: SortDir = SortDir.DESC
