"""Expense DTOs."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum, unique
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import InitErrorDetails, PydanticCustomError

from tuttitrip.shared.pagination.schemas import ListFilters, PageParams, SortDir

Money = Annotated[Decimal, Field(max_digits=12, decimal_places=2)]
ShareValue = Annotated[Decimal, Field(max_digits=10, decimal_places=4)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Rate = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=8)]


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
    CURRENCY_UNSUPPORTED = "expense.currency_unsupported"
    RATE_NOT_FOUND = "expense.rate_not_found"
    RATE_UNAVAILABLE = "expense.rate_unavailable"
    PAYER_NOT_ON_TRIP = "expense.payer_not_on_trip"
    PARTICIPANTS_REQUIRED = "expense.participants_required"
    PARTICIPANT_NOT_ON_TRIP = "expense.participant_not_on_trip"
    PARTICIPANT_DUPLICATED = "expense.participant_duplicated"
    SHARE_VALUE_REQUIRED = "expense.share_value_required"
    SHARE_VALUE_NOT_ALLOWED = "expense.share_value_not_allowed"
    PERCENT_SUM = "expense.percent_sum"
    SHARE_VALUE_NOT_POSITIVE = "expense.share_value_not_positive"


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
        description=(
            "Empty for `equal`, percent for `percent` (all must sum to exactly 100), "
            "weight for `weights`."
        ),
    )


class ExpenseCreate(BaseModel):
    """POST payload: one expense of the trip."""

    model_config = ConfigDict(extra="forbid")

    payer_profile_id: UUID
    amount: Money = Field(description="Positive, at most 2 decimal places.")
    currency: Currency | None = Field(
        default=None,
        description=(
            "Currency of `amount` (the trip's if empty). A foreign currency is "
            "converted at the NBP average rate of `spent_on`."
        ),
    )
    description: str = Field(default="", max_length=500)
    spent_on: date = Field(description="The day the money was spent.")
    category: ExpenseCategory | None = None
    split_method: SplitMethod = SplitMethod.EQUAL
    participants: list[ShareInput] = Field(
        min_length=1, description="Who shares the cost; anyone left out does not pay."
    )
    manual_rate: Rate | None = Field(
        default=None,
        description=(
            "Trip-currency units per unit of `currency`. Used instead of the NBP "
            "rate (source `manual`), e.g. when NBP does not answer (422 "
            "`expense.rate_unavailable`). Ignored for the trip's own currency."
        ),
    )


class ExpenseUpdate(BaseModel):
    """PATCH payload: send only what changes (``participants`` replaces the list)."""

    model_config = ConfigDict(extra="forbid")

    payer_profile_id: UUID | None = None
    amount: Money | None = None
    currency: Currency | None = Field(
        default=None, description="Only checked against the trip when sent."
    )
    description: str | None = Field(default=None, max_length=500)
    spent_on: date | None = None
    category: ExpenseCategory | None = None
    split_method: SplitMethod | None = None
    participants: list[ShareInput] | None = Field(default=None, min_length=1)
    manual_rate: Rate | None = Field(
        default=None,
        description=(
            "Trip-currency units per unit of `currency`. Used instead of the NBP "
            "rate (source `manual`), e.g. when NBP does not answer (422 "
            "`expense.rate_unavailable`). Ignored for the trip's own currency."
        ),
    )

    @model_validator(mode="after")
    def _no_null_for_required(self) -> Self:
        # `category` is the only field that may be cleared with an explicit null.
        # The code goes in the error `type`, which the global 422 handler keeps.
        errors = [
            InitErrorDetails(
                type=PydanticCustomError(
                    ExpenseErrorCode.NULL_NOT_ALLOWED.value,
                    "{message}",
                    {"message": f"{field} cannot be null"},
                ),
                loc=(field,),
                input=None,
            )
            for field in sorted(self.model_fields_set - {"category"})
            if getattr(self, field) is None
        ]
        if errors:
            title = "ExpenseUpdate"
            raise ValidationError.from_exception_data(title, errors)
        return self


class ParticipantRead(BaseModel):
    """A participant with their entered share and the amount they owe."""

    profile_id: UUID
    value: Decimal | None = Field(description="As entered; empty for `equal`.")
    amount: Decimal = Field(description="Their part of the cost, rounded to cents.")


class ExchangeRateRead(BaseModel):
    """The rate an expense in a foreign currency was converted at (never changes)."""

    rate: Decimal = Field(description="Trip-currency units per unit of `currency`.")
    source: Literal["nbp", "manual"]
    table_no: str | None = Field(
        description="NBP table number(s), e.g. `187/A/NBP/2026`; empty if manual."
    )
    effective_date: date | None = Field(
        description="Day of the NBP quote (may precede `spent_on`); empty if manual."
    )


class ExpenseRead(BaseModel):
    """An expense as returned by the API."""

    id: UUID
    trip_id: UUID
    payer_profile_id: UUID
    amount: Decimal = Field(description="In `currency`, as paid.")
    currency: str
    trip_amount: Decimal = Field(
        description="`amount` in the trip's currency; settlement uses this."
    )
    exchange_rate: ExchangeRateRead | None = Field(
        description="Set when `currency` differs from the trip's."
    )
    description: str
    spent_on: date
    category: ExpenseCategory | None
    split_method: SplitMethod
    participants: list[ParticipantRead] = Field(
        description="Their parts are in the trip's currency."
    )
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


class ExpenseTextRequest(BaseModel):
    """POST payload: one sentence describing an expense."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        min_length=1,
        max_length=500,
        description='E.g. "obiad 142 zł, płaciła Kasia, bez Ani".',
    )


class DraftIssueRead(BaseModel):
    """Something in a draft the person must confirm."""

    code: Literal[
        "name_not_on_trip",
        "name_ambiguous",
        "payer_missing",
        "participants_empty",
        "low_confidence",
    ]
    name: str | None = Field(description="The name as written, if it is about one.")
    candidates: list[UUID] = Field(description="Profiles the name might mean.")


class ExpenseDraft(BaseModel):
    """An expense read from a text, for the form; nothing is saved."""

    amount: Decimal
    currency: str
    description: str
    spent_on: date = Field(description="Today unless the text says otherwise.")
    payer_profile_id: UUID | None
    participants: list[UUID]
    needs_confirmation: bool = Field(
        description="True when `issues` is not empty: ask the person to confirm."
    )
    issues: list[DraftIssueRead]


class ExpenseDraftState(BaseModel):
    """Progress of reading a text; `draft` is set once `status` is `ready`."""

    status: Literal["pending", "ready", "failed"]
    draft: ExpenseDraft | None
