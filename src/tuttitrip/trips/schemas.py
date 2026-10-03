"""Trip DTOs."""

from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum, unique
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    computed_field,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError


@unique
class TripRole(StrEnum):
    """A person's role on one trip: ``member < co_host < host``.

    The creator is the host. Trip roles are object-level: they say what a
    user may do on *this* trip, on top of the global feature permissions.
    """

    MEMBER = "member"
    CO_HOST = "co_host"
    HOST = "host"

    @property
    def rank(self) -> int:
        """Position in the ordering (higher means more rights).

        Returns:
            0 for member, 1 for co-host, 2 for host.
        """
        return list(TripRole).index(self)

    def satisfies(self, required: TripRole) -> bool:
        """Whether this role is enough for ``required``.

        Args:
            required: The minimum role an operation needs.

        Returns:
            True when this role ranks at least as high.
        """
        return self.rank >= required.rank


class TripCreate(BaseModel):
    """Payload for creating a trip."""

    name: str = Field(min_length=1, max_length=200)
    destination: str | None = Field(default=None, max_length=200)


SLUG = r"^[a-z0-9-]+$"  # same rule as city slugs: lowercase, no diacritics, "-"
FLEX = (
    "Flex of E6 in percent (0-50), the solver divides it by 100: "
    "B_max = B_do * (1 + flex_pct / 100)."
)
ALPHA = "Group goal alpha of E5 (0-3); 1 balances fairness and total utility."
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


_REQUIRED_WHEN_SENT = (
    "name",
    "day_start",
    "day_end",
    "budget_flex_pct",
    "fairness_alpha",
)
# (lower, upper) pairs: both set or both empty (E6: B_od <= B_do).
_PAIRS = (
    ("start_date", "end_date"),
    ("budget_total_min", "budget_total_max"),
    ("budget_day_min", "budget_day_max"),
)


_TEMPLATE = "{message}"  # pydantic fills it from the context


def _error(field: str, message: str) -> InitErrorDetails:
    return InitErrorDetails(
        type=PydanticCustomError("value_error", _TEMPLATE, {"message": message}),
        loc=(field,),
        input=None,
    )


def check_trip(trip: TripUpdate, *, complete: bool) -> None:
    """Check the cross-field rules of trip details.

    Every error carries the offending field as ``loc``, in one shape.

    Args:
        trip: The payload (``complete=False``) or the merged trip state.
        complete: Also require dates and each budget range to be set in pairs.
            Off for a PATCH body, which may send one half of a pair.

    Raises:
        ValidationError: One error per broken rule, ``loc`` is ``(field,)``.
    """
    errors = [
        _error(field, f"{field} cannot be null")
        for field in _REQUIRED_WHEN_SENT
        if field in trip.model_fields_set and getattr(trip, field) is None
    ]
    for lower, upper in _PAIRS:
        lo, hi = getattr(trip, lower), getattr(trip, upper)
        if complete and (lo is None) != (hi is None):
            missing = lower if lo is None else upper
            errors.append(_error(missing, f"{missing} is required with its pair"))
        elif lo is not None and hi is not None and hi < lo:
            errors.append(_error(upper, f"{upper} must not be before {lower}"))
    if trip.day_start and trip.day_end and trip.day_end <= trip.day_start:
        errors.append(_error("day_end", "day_end must be after day_start"))
    if errors:
        title = "TripUpdate"
        raise ValidationError.from_exception_data(title, errors)


class TripUpdate(BaseModel):
    """PATCH payload: only the fields that are sent change; ``null`` clears.

    The service checks the merged state with ``check_trip(complete=True)``, so
    a lone ``end_date`` is compared with the stored ``start_date``.
    """

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    destination: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    day_start: time | None = None
    day_end: time | None = None
    city_slug: str | None = Field(default=None, max_length=64, pattern=SLUG)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    budget_total_min: Money | None = None
    budget_total_max: Money | None = None
    budget_day_min: Money | None = None
    budget_day_max: Money | None = None
    budget_flex_pct: int | None = Field(default=None, ge=0, le=50, description=FLEX)
    fairness_alpha: float | None = Field(default=None, ge=0, le=3, description=ALPHA)

    @model_validator(mode="after")
    def _check(self) -> Self:
        check_trip(self, complete=False)
        return self


class TripDetails(BaseModel):
    """A trip's own data, read from the ORM model."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    destination: str | None
    created_at: datetime
    start_date: date | None
    end_date: date | None
    day_start: time
    day_end: time
    city_slug: str | None
    currency: str | None
    budget_total_min: Decimal | None
    budget_total_max: Decimal | None
    budget_day_min: Decimal | None
    budget_day_max: Decimal | None
    budget_flex_pct: int = Field(description=FLEX)
    fairness_alpha: float = Field(description=ALPHA)

    @computed_field
    @property
    def kind(self) -> Literal["trip", "outing"]:
        """``outing`` for a single day without a stay, otherwise ``trip``.

        Returns:
            The kind derived from the dates.
        """
        same_day = self.start_date is not None and self.start_date == self.end_date
        return "outing" if same_day else "trip"


class TripRead(TripDetails):
    """A trip as returned by the API."""

    my_role: TripRole = Field(description="The caller's role on this trip.")


class TripMembership(BaseModel):
    """Proof that a user may act on a trip, as checked by ``TripAccess``."""

    trip_id: UUID
    sub: str
    role: TripRole
