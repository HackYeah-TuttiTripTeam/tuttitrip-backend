"""Trip DTOs."""

from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum, unique
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


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


Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


class TripUpdate(BaseModel):
    """PATCH payload: only the fields that are sent change; ``null`` clears.

    The service also validates the merged state with this model, so a lone
    ``end_date`` is checked against the stored ``start_date``.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    destination: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    day_start: time | None = None
    day_end: time | None = None
    city_slug: str | None = Field(default=None, max_length=100)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    budget_total_min: Money | None = None
    budget_total_max: Money | None = None
    budget_day_min: Money | None = None
    budget_day_max: Money | None = None
    budget_flex_pct: int | None = Field(default=None, ge=0, le=50)
    fairness_alpha: float | None = Field(default=None, ge=0, le=3)

    @model_validator(mode="after")
    def _check_ranges(self) -> Self:
        pairs = (
            ("end_date", "start_date", "end_date must not be before start_date"),
            ("day_end", "day_start", "day_end must be after day_start"),
            (
                "budget_total_max",
                "budget_total_min",
                "budget_total_max must not be below budget_total_min",
            ),
            (
                "budget_day_max",
                "budget_day_min",
                "budget_day_max must not be below budget_day_min",
            ),
        )
        for upper, lower, message in pairs:
            hi, lo = getattr(self, upper, None), getattr(self, lower, None)
            if hi is None or lo is None:
                continue
            if hi < lo or (upper == "day_end" and hi == lo):
                raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _required_fields_not_null(self) -> Self:
        for field in (
            "name",
            "day_start",
            "day_end",
            "budget_flex_pct",
            "fairness_alpha",
        ):
            if field in self.model_fields_set and getattr(self, field) is None:
                msg = f"{field} cannot be null"
                raise ValueError(msg)
        return self


class TripRead(BaseModel):
    """A trip as returned by the API."""

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
    budget_flex_pct: int = Field(description="Flex of E6 in percent (0-50).")
    fairness_alpha: float = Field(description="Group goal alpha of E5 (0-3).")
    my_role: TripRole = Field(description="The caller's role on this trip.")

    @computed_field
    @property
    def kind(self) -> Literal["trip", "outing"]:
        """``outing`` for a single day without a stay, otherwise ``trip``.

        Returns:
            The kind derived from the dates.
        """
        same_day = self.start_date is not None and self.start_date == self.end_date
        return "outing" if same_day else "trip"


class TripMembership(BaseModel):
    """Proof that a user may act on a trip, as checked by ``TripAccess``."""

    trip_id: UUID
    sub: str
    role: TripRole
