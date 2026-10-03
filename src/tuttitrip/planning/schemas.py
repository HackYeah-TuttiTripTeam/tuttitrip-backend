"""Planning DTOs."""

from datetime import date, time
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tuttitrip.places.schemas import PlaceRead, PlaceTag
from tuttitrip.profiles.preferences.schemas import ImportancePool, MinTag
from tuttitrip.shared.jobs.contracts import ProviderName

Vote = Literal[-1, 0, 1]
"""``v_ip``: -1 do not want, 0 neutral, +1 want. A missing key means no vote."""


class TripPlan(BaseModel):
    """A short trip plan suggested to the user."""

    destination: str = Field(min_length=1, description="Destination city or region.")
    days: int = Field(ge=1, le=30, description="Trip length in days.")
    highlights: list[str] = Field(
        default_factory=list,
        description="Places or activities worth visiting.",
    )


class PlanJobRequest(BaseModel):
    """Ask the worker to draft a plan for one of the caller's trips."""

    trip_id: UUID
    request: str = Field(min_length=1, max_length=4000)
    provider: ProviderName = Field(
        default="openrouter", description="LLM backend: OpenRouter or the local model."
    )


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class PlanningPerson(_Frozen):
    """One person as the algorithm sees them (docs/algorytm.md, section 2)."""

    id: UUID
    weight: float = Field(gt=0, description="w_i: child 2, adult 1.")
    interests: dict[PlaceTag, Annotated[float, Field(ge=0, le=1)]] = Field(
        default_factory=dict, description="Interest profile I_i; empty means unknown."
    )
    pool: ImportancePool = Field(description="a_ij before dividing by the total.")
    segment_km: float = Field(gt=0, description="s_i: longest walk in one go.")
    daily_km: float = Field(gt=0, description="D_i: daily walking distance.")
    active_min: int = Field(gt=0, description="A_i: active minutes per day.")
    stairs_sensitivity: float = Field(
        ge=0,
        le=1,
        description="Effective sensitivity (1.0 with the stairs or wheelchair limit).",
    )
    queue_patience_min: int = Field(ge=0, description="Queue the person tolerates.")
    floor: float = Field(ge=0, le=100, description="f_i: minimum welfare.")
    votes: dict[UUID, Vote] = Field(
        default_factory=dict, description="v_ip by place id."
    )
    vetoes: frozenset[UUID] = frozenset()
    min_tags: tuple[MinTag, ...] = ()


class PlanningTrip(_Frozen):
    """The trip as the algorithm sees it."""

    days: tuple[date, ...] = Field(min_length=1)
    timezone: str = Field(description="IANA zone of the city; hours are in it.")
    day_start: time
    day_end: time
    budget_from: Decimal = Field(ge=0, description="B_od.")
    budget_to: Decimal = Field(ge=0, description="B_do.")
    flex_pct: int = Field(ge=0, description="flex in percent.")
    has_lodging: bool = Field(
        description="Whether the lodging domain is active (there are nights)."
    )

    @property
    def budget_max(self) -> Decimal:
        """``B_max = B_do * (1 + flex)``."""
        return self.budget_to * (100 + self.flex_pct) / 100

    @model_validator(mode="after")
    def _budget_order(self) -> Self:
        if self.budget_from > self.budget_to:
            msg = "budget_from must not exceed budget_to"
            raise ValueError(msg)
        return self


class PlanningInput(_Frozen):
    """Everything the algorithm needs; no database and no calls."""

    trip: PlanningTrip
    people: tuple[PlanningPerson, ...] = Field(min_length=1)
    places: tuple[PlaceRead, ...]
    must: frozenset[UUID] = frozenset()

    @model_validator(mode="after")
    def _unique_people(self) -> Self:
        if len({p.id for p in self.people}) != len(self.people):
            msg = "People must have unique ids"
            raise ValueError(msg)
        return self


class PlaceExplain(_Frozen):
    """Card "why this place" for one person (docs/algorytm.md, section 10)."""

    person_id: UUID
    place_id: UUID
    match: float = Field(ge=0, le=1, description="m_ip: fit to interests and vote.")
    effort: float = Field(ge=0, le=1, description="e_ip: distance, stairs, queue.")
    utility: float = Field(ge=0, le=100, description="u_ip, without cost.")
