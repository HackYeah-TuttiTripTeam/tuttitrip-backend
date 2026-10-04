"""Expense DTOs."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum, unique
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tuttitrip.shared.pagination.schemas import ListFilters, PageParams, SortDir

Money = Annotated[Decimal, Field(max_digits=12, decimal_places=2)]
ShareValue = Annotated[Decimal, Field(max_digits=10, decimal_places=4)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


@unique
class SplitMethod(StrEnum):
    """How an expense is divided among its participants.

    A person who does not take part has no share row. The shares are costs
    entered on the expense, not the voice weights of the planning algorithm.
    """

    EQUAL = "equal"
    PERCENT = "percent"
    WEIGHTS = "weights"


@unique
class ExpenseCategory(StrEnum):
    """What the money was spent on (optional label of an expense)."""

    FOOD = "food"
    TRANSPORT = "transport"
    LODGING = "lodging"
    ACTIVITIES = "activities"
    SHOPPING = "shopping"
    OTHER = "other"


@unique
class ExpenseErrorCode(StrEnum):
    """Stable code of an expense rule violation, sent as the 422 item's ``type``.

    Clients map errors by this code and ``loc``, never by ``msg``.
    """

    NULL_NOT_ALLOWED = "expense.null_not_allowed"
    AMOUNT_NOT_POSITIVE = "expense.amount_not_positive"
    CURRENCY_REQUIRED = "expense.currency_required"
    CURRENCY_MISMATCH = "expense.currency_mismatch"
    PAYER_NOT_ON_TRIP = "expense.payer_not_on_trip"
    PARTICIPANTS_REQUIRED = "expense.participants_required"
    PARTICIPANT_NOT_ON_TRIP = "expense.participant_not_on_trip"
    PARTICIPANT_DUPLICATED = "expense.participant_duplicated"
    SHARE_VALUE_REQUIRED = "expense.share_value_required"
    SHARE_VALUE_NOT_ALLOWED = "expense.share_value_not_allowed"
    PERCENT_SUM = "expense.percent_sum"
    WEIGHT_NOT_POSITIVE = "expense.weight_not_positive"


class ExpenseValidationError(BaseModel):
    """One 422 item of an expense rule violation."""

    type: ExpenseErrorCode
    loc: list[str] = Field(description='`["body", field]`')
    msg: str = Field(description="For people; may change, do not parse it.")


class ExpenseValidationErrors(BaseModel):
    """The 422 body of ``POST`` and ``PATCH`` ``/trips/{trip_id}/expenses``."""

    detail: list[ExpenseValidationError]


class ShareInput(BaseModel):
    """One participant: a trip profile and, unless equal, their share."""

    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    value: ShareValue | None = Field(
        default=None,
        description="Empty for `equal`, percent for `percent`, weight for `weights`.",
    )


class ExpenseCreate(BaseModel):
    """POST payload: one expense of the trip."""

    model_config = ConfigDict(extra="forbid")

    payer_profile_id: UUID
    amount: Money = Field(description="Positive, at most 2 decimal places.")
    currency: Currency | None = Field(
        default=None, description="The trip's currency (taken from the trip if empty)."
    )
    description: str = Field(default="", max_length=500)
    spent_on: date = Field(description="The day the money was spent.")
    category: ExpenseCategory | None = None
    split_method: SplitMethod = SplitMethod.EQUAL
    participants: list[ShareInput] = Field(
        min_length=1, description="Who shares the cost; anyone left out does not pay."
    )


class ExpenseUpdate(BaseModel):
    """PATCH payload: send only what changes (``participants`` replaces the list)."""

    model_config = ConfigDict(extra="forbid")

    payer_profile_id: UUID | None = None
    amount: Money | None = None
    currency: Currency | None = None
    description: str | None = Field(default=None, max_length=500)
    spent_on: date | None = None
    category: ExpenseCategory | None = None
    split_method: SplitMethod | None = None
    participants: list[ShareInput] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _no_null_for_required(self) -> Self:
        # `category` is the only field that may be cleared with an explicit null.
        for field in self.model_fields_set - {"category"}:
            if getattr(self, field) is None:
                msg = f"{field} cannot be null"
                raise ValueError(msg)
        return self


class ParticipantRead(BaseModel):
    """A participant with their entered share and the amount they owe."""

    profile_id: UUID
    value: Decimal | None = Field(description="As entered; empty for `equal`.")
    amount: Decimal = Field(description="Their part of the cost, rounded to cents.")


class ExpenseRead(BaseModel):
    """An expense as returned by the API."""

    id: UUID
    trip_id: UUID
    payer_profile_id: UUID
    amount: Decimal
    currency: str
    description: str
    spent_on: date
    category: ExpenseCategory | None
    split_method: SplitMethod
    participants: list[ParticipantRead]
    created_by_sub: str
    created_at: datetime


@unique
class ExpenseSort(StrEnum):
    """Sort keys of the expense list."""

    SPENT_ON = "spent_on"
    AMOUNT = "amount"
    CREATED_AT = "created_at"


class ExpenseFilters(ListFilters):
    """Filters of the expense list (shared with any future bulk operation)."""

    date_from: date | None = Field(
        default=None, description="From this day, inclusive."
    )
    date_to: date | None = Field(default=None, description="Up to this day, inclusive.")
    payer_profile_id: UUID | None = None
    participant_profile_id: UUID | None = Field(
        default=None, description="Expenses this person takes part in."
    )
    category: ExpenseCategory | None = None


class ExpenseQuery(PageParams, ExpenseFilters):
    """Query of ``GET /trips/{trip_id}/expenses``."""

    sort: ExpenseSort = ExpenseSort.SPENT_ON
    dir: SortDir = SortDir.DESC
