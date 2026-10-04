"""Settlement DTOs."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum, unique
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tuttitrip.shared.pagination.schemas import ListFilters, PageParams, SortDir

PaymentAmount = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


class BalanceRead(BaseModel):
    """Net balance of one person: positive means they are owed money."""

    profile_id: UUID
    amount: Decimal = Field(description="In the trip's currency, to the cent.")


class TransferRead(BaseModel):
    """One payment that settles part of the trip."""

    from_profile_id: UUID
    to_profile_id: UUID
    amount: Decimal = Field(description="In the trip's currency, to the cent.")


class SettlementRead(BaseModel):
    """Balances and the smallest list of transfers of a trip."""

    currency: str | None
    total_spent: Decimal = Field(description="Sum of all expenses.")
    balances: list[BalanceRead] = Field(
        description="Every person on the trip, ordered by profile id; sums to 0.00."
    )
    transfers: list[TransferRead] = Field(
        description="What is still to pay, after the payments marked as paid."
    )
    closed_at: datetime | None = Field(
        description="Set while the host has the settlement closed (no expense changes)."
    )


@unique
class PaymentErrorCode(StrEnum):
    """Stable code of a payment rule violation, sent as the 422 item's ``type``."""

    SAME_PERSON = "payment.same_person"
    PERSON_NOT_ON_TRIP = "payment.person_not_on_trip"


class PaymentValidationError(BaseModel):
    """One 422 item of a payment rule violation."""

    type: PaymentErrorCode
    loc: list[str] = Field(description='`["body", field]`')
    msg: str = Field(description="For people; may change, do not parse it.")


class PaymentValidationErrors(BaseModel):
    """The 422 body of ``POST .../settlement/payments``."""

    detail: list[PaymentValidationError]


class PaymentCreate(BaseModel):
    """POST payload: a transfer (or a part of it) that has been paid."""

    model_config = ConfigDict(extra="forbid")

    from_profile_id: UUID
    to_profile_id: UUID
    amount: PaymentAmount = Field(
        description="May be a part of the transfer or more than the debt."
    )
    paid_on: date | None = Field(default=None, description="Today if empty.")


class PaymentRead(BaseModel):
    """A payment marked as made."""

    id: UUID
    trip_id: UUID
    from_profile_id: UUID
    to_profile_id: UUID
    amount: Decimal
    paid_on: date
    marked_by_sub: str
    created_at: datetime


@unique
class PaymentSort(StrEnum):
    """Sort keys of the payment list."""

    PAID_ON = "paid_on"
    AMOUNT = "amount"
    CREATED_AT = "created_at"


class PaymentFilters(ListFilters):
    """Filters of the payment list."""

    from_profile_id: UUID | None = None
    to_profile_id: UUID | None = None


class PaymentQuery(PageParams, PaymentFilters):
    """Query of ``GET /trips/{trip_id}/expenses/settlement/payments``."""

    sort: PaymentSort = PaymentSort.PAID_ON
    dir: SortDir = SortDir.DESC
