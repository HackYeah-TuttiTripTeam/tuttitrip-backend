"""DTOs of the daily budget (backend#89). Money is ``Decimal`` in the trip currency."""

import datetime as dt
from decimal import Decimal
from enum import StrEnum, unique
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field

from tuttitrip.planning.plans.schemas import ApprovalStatus

Amount = Annotated[Decimal, Field(decimal_places=2, max_digits=14)]


@unique
class ProposalSkip(StrEnum):
    """Why no proposal was made; the UI writes the text (PL/EN)."""

    DISABLED = "disabled"
    """The trip's setting "propose cheaper alternatives" is off."""
    NO_OVERRUN = "no_overrun"
    """No day went over its budget."""
    NO_DAYS_LEFT = "no_days_left"
    """The day that went over is the last one."""
    NO_SAVING = "no_saving"
    """The solver found nothing cheaper for the rest."""


class DayBudgetRead(BaseModel):
    """One day against its budget."""

    index: int = Field(ge=1, description="1-based day number.")
    date: dt.date
    budget_from: Amount = Field(description="B_od of the day.")
    budget_to: Amount = Field(description="B_do of the day.")
    budget_max: Amount = Field(
        description="B_max of the day: B_do + the trip's margin."
    )
    spent: Amount = Field(
        description=(
            "Expenses of the day that count: food, transport, activities and "
            "uncategorised ones."
        )
    )
    outside_plan: Amount = Field(
        description="Shopping, lodging and other expenses; they do not take the budget."
    )
    remaining: Amount = Field(description="B_do - spent; negative when over.")
    remaining_max: Amount = Field(description="B_max - spent; negative when over.")
    over_budget: bool = Field(description="spent > B_do.")
    over_max: bool = Field(description="spent > B_max.")


class BudgetProposalRead(BaseModel):
    """A cheaper plan for the days after an overrun, waiting for the host.

    Stored as an alternative of the latest plan version (``GET
    /trips/{trip_id}/plans/{plan_id}`` returns it with its days and fairness
    ledger); it replaces nothing until the host approves.
    """

    plan_id: UUID
    version: int = Field(ge=1, description="The plan version it is an alternative of.")
    from_day: int = Field(ge=2, description="First day the proposal replaces.")
    from_date: dt.date
    overrun_days: list[int] = Field(description="Days that went over B_do.")
    budget_to: Amount = Field(description="B_do left for the rest after the spending.")
    cost: Amount = Field(description="Cost of the proposed days.")
    previous_cost: Amount = Field(description="Cost of the same days in the plan.")
    cost_delta: Annotated[Decimal, Field(decimal_places=2, max_digits=14)] = Field(
        description="cost - previous_cost; negative: cheaper."
    )
    min_r: float = Field(ge=0, le=1, description="min r of the proposed days.")
    previous_min_r: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="min r of the same days in the plan; null if it no longer fits.",
    )
    min_r_delta: float | None = Field(default=None, description="min_r - previous.")
    needs_approval: bool = Field(
        description="The proposal needs going over B_do: it goes through E6 (kappa)."
    )
    kappa: Decimal | None = Field(
        default=None, description="Price per point (E6); set iff needs_approval."
    )
    approval_status: ApprovalStatus


class BudgetDaysRead(BaseModel):
    """The budget of every day of the trip and the proposal, if there is one."""

    currency: str
    propose_cheaper_alternatives: bool
    total_spent: Amount = Field(description="Counted expenses of the whole trip.")
    days: list[DayBudgetRead]
    proposal: BudgetProposalRead | None = None


class ProposalOutcome(BaseModel):
    """Result of asking for a proposal."""

    created: bool = Field(description="False: it existed already (same data).")
    skipped: ProposalSkip | None = Field(
        default=None, description="Why there is no proposal; null with one."
    )
    proposal: BudgetProposalRead | None = None
