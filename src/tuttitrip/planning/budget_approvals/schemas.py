"""Budget consent DTOs."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from tuttitrip.shared.pagination.schemas import ListFilters, PageParams, SortDir

REASON_MAX_LENGTH = 500

Reason = Annotated[str, Field(min_length=1, max_length=REASON_MAX_LENGTH)]


@unique
class BudgetApprovalStatus(StrEnum):
    """State of the question "may the plan cost more than ``B_do``"."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@unique
class BudgetApprovalErrorCode(StrEnum):
    """Stable code of a consent error, sent as ``detail.code``."""

    NOT_PENDING = "budget_approval.not_pending"


class BudgetDecisionCreate(BaseModel):
    """The host's decision.

    The reason is optional: the amount and the person who gains are logged anyway.
    """

    reason: Reason | None = None


class BudgetApprovalRead(BaseModel):
    """A consent question with the facts shown on the approval card."""

    id: UUID
    trip_id: UUID
    flex_plan_id: UUID = Field(description="P_flex, over B_do.")
    strict_plan_id: UUID = Field(description="P_strict, within B_do.")
    currency: str
    over_budget: Decimal = Field(description="Amount above B_do of P_flex.")
    kappa: Decimal = Field(description="Price of a point, currency per point.")
    gain_profile_id: UUID | None = Field(description="Who gains most from going over.")
    gain_profile_name: str | None
    gain_points: float | None
    status: BudgetApprovalStatus
    active_plan_id: UUID | None = Field(
        description="The plan in force after the decision: P_flex or P_strict."
    )
    created_at: datetime
    decided_by_sub: str | None
    decided_at: datetime | None


@unique
class BudgetApprovalSort(StrEnum):
    """Sort keys of the list."""

    CREATED_AT = "created_at"


class BudgetApprovalFilters(ListFilters):
    """Filters of the list."""

    status: BudgetApprovalStatus | None = None


class BudgetApprovalQuery(PageParams, BudgetApprovalFilters):
    """Query of ``GET .../budget-approvals``."""

    sort: BudgetApprovalSort = BudgetApprovalSort.CREATED_AT
    dir: SortDir = SortDir.DESC


class NotPendingDetail(BaseModel):
    """Why the decision was refused; clients map by ``code``."""

    code: Literal[BudgetApprovalErrorCode.NOT_PENDING] = (
        BudgetApprovalErrorCode.NOT_PENDING
    )
    message: str = Field(description="For developers; clients map by code.")
    status: BudgetApprovalStatus


class NotPendingError(BaseModel):
    """409 body: somebody decided already, or the plan was recomputed."""

    detail: NotPendingDetail
