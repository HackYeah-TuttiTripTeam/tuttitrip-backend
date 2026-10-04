"""Planning DTOs."""

from datetime import date, time
from decimal import Decimal
from enum import StrEnum, unique
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tuttitrip.accommodation.schemas import RequirementStatus
from tuttitrip.places.schemas import PlaceRead, PlaceTag, TransitFareRead
from tuttitrip.profiles.feedback.schemas import ReasonCode
from tuttitrip.profiles.preferences.schemas import ImportancePool, MinTag
from tuttitrip.shared.jobs.contracts import ProviderName

MAX_WEIGHT_RATIO = 3
"""Section 2: ``max w / min w`` is at most 3."""

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
    age: int = Field(ge=0, le=120, description="Picks the ticket category.")
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
    nap_start: time | None = Field(default=None, description="Fixed break for the day.")
    nap_minutes: int = Field(default=0, ge=0)
    floor: float = Field(ge=0, le=100, description="f_i: minimum welfare.")
    votes: dict[UUID, Vote] = Field(
        default_factory=dict, description="v_ip by place id."
    )
    vote_reasons: dict[UUID, ReasonCode] = Field(
        default_factory=dict, description="Why the person is against a place."
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
    currency: str = Field(default="PLN", description="ISO 4217; prices in it count.")

    @property
    def budget_max(self) -> Decimal:
        """``B_max = B_do * (1 + flex)``."""
        return self.budget_to * (100 + self.flex_pct) / 100

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.budget_from > self.budget_to:
            msg = "budget_from must not exceed budget_to"
            raise ValueError(msg)
        if self.day_start >= self.day_end:
            msg = "day_start must be before day_end"
            raise ValueError(msg)
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            msg = f"Unknown time zone: {self.timezone}"
            raise ValueError(msg) from error
        return self


class PlanningInput(_Frozen):
    """Everything the algorithm needs; no database and no calls."""

    trip: PlanningTrip
    people: tuple[PlanningPerson, ...] = Field(min_length=1)
    places: tuple[PlaceRead, ...]
    must: frozenset[UUID] = frozenset()
    lodgings: tuple[LodgingOption, ...] = Field(
        default=(),
        description="Options for the lodging base; the solver picks one (section 9).",
    )
    fares: tuple[TransitFareRead, ...] = Field(
        default=(),
        description="The city's public transport tariff (shown, never in c(P)).",
    )
    blocked: frozenset[UUID] = Field(
        default=frozenset(), description="Places the host blocked (E0, like a veto)."
    )

    @model_validator(mode="after")
    def _valid_people(self) -> Self:
        if len({p.id for p in self.people}) != len(self.people):
            msg = "People must have unique ids"
            raise ValueError(msg)
        weights = [p.weight for p in self.people]
        if max(weights) > MAX_WEIGHT_RATIO * min(weights):
            msg = f"Weights may differ at most {MAX_WEIGHT_RATIO} times"
            raise ValueError(msg)
        return self


class PlaceExplain(_Frozen):
    """Card "why this place" for one person (docs/algorytm.md, section 10)."""

    person_id: UUID
    place_id: UUID
    match: float = Field(ge=0, le=1, description="m_ip: fit to interests and vote.")
    effort: float = Field(ge=0, le=1, description="e_ip: distance, stairs, queue.")
    utility: float = Field(ge=0, le=100, description="u_ip, without cost.")


class DayPlan(_Frozen):
    """One day of a plan: the places and the quantities E2 "tempo" needs.

    ``distance_km`` (``L_d``) and ``active_min`` (``A_d``) come from
    ``schedule.schedule_day``; an empty day has zeros.
    """

    place_ids: tuple[UUID, ...] = ()
    distance_km: float = Field(default=0, ge=0)
    active_min: int = Field(default=0, ge=0)


class LodgingOutcome(_Frozen):
    """One requirement of the trip checked against a lodging (3-state contract)."""

    feature: str
    hard: bool
    status: RequirementStatus


class LodgingOption(_Frozen):
    """A place to sleep: its night price and how it meets the requirements (E2).

    ``outcomes`` are the trip's requirements checked against this option; hard
    ones multiply into ``S_h``, soft ones average (met 1, unconfirmed 0.4,
    unmet 0). The night price is taken as given: E6 has no markup for lodging.
    """

    place_id: UUID
    name: str
    lat: float
    lon: float
    price_per_night: Decimal = Field(ge=0)
    verified: bool = False
    outcomes: tuple[LodgingOutcome, ...] = ()


class LodgingStay(_Frozen):
    """The lodging base of the trip as a single price (docs/algorytm.md, section 9).

    The night price is taken as given: E6 has no markup for lodging, so an
    unverified night price is not raised by ``delta``. ``place_id`` is set when
    the base is a catalog place.
    """

    nights: int = Field(gt=0)
    price_per_night: Decimal = Field(ge=0)
    place_id: UUID | None = None


class DomainScores(_Frozen):
    """Satisfaction ``q_ij`` of one person in the five domains and welfare ``u_i``.

    A domain that does not apply (``lodging`` without nights) is None and the
    pool ignores it. All values are rounded to four places.
    """

    person_id: UUID
    lodging: float | None = Field(ge=0, le=100)
    food: float = Field(ge=0, le=100)
    attractions: float = Field(ge=0, le=100)
    pace: float = Field(ge=0, le=100)
    cost: float = Field(ge=0, le=100)
    welfare: float = Field(ge=0, le=100, description="u_i of E3.")

    @property
    def lodging_applicable(self) -> bool:
        """False when the trip has no nights ("nie dotyczy")."""
        return self.lodging is not None


@unique
class WhatIfField(StrEnum):
    """An answer of the interview whose effect on the plan can be measured."""

    DATES = "dates"
    PEOPLE = "people"
    BUDGET = "budget"
    PACE = "pace"
    IMPORTANCE = "importance"
    REQUIREMENTS = "requirements"
    INTERESTS = "interests"


class WhatIfTarget(_Frozen):
    """One question to measure: a field, and a person for the personal ones."""

    field: WhatIfField
    person_id: UUID | None = None
