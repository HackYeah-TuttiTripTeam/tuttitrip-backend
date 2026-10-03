"""Plan DTOs: the response shape of ``docs/algorytm.md``, section 10.

Symbols follow the specification: ``u`` (welfare), ``u_star`` (best alone),
``r`` (share of it), ``q`` (domain satisfaction), ``S_h`` (lodging contract),
``kappa`` (price per point). Fields filled by later issues are optional or
empty lists, so their arrival does not change the shape.
"""

import datetime as dt
from enum import StrEnum, unique
from uuid import UUID

from pydantic import BaseModel, Field

from tuttitrip.accommodation.schemas import RequirementStatus


@unique
class Domain(StrEnum):
    """The five domains of satisfaction ``q_ij`` (E2)."""

    ATTRACTIONS = "attractions"
    FOOD = "food"
    PACE = "pace"
    COST = "cost"
    LODGING = "lodging"


@unique
class PlaceKind(StrEnum):
    """Role of a place in a day."""

    ATTRACTION = "attraction"
    FOOD = "food"


@unique
class BudgetZone(StrEnum):
    """Where the plan cost falls against the budget (E2 cost, E6)."""

    BELOW_B_FROM = "below_b_from"
    UP_TO_B_TO = "up_to_b_to"
    IN_MARGIN = "in_margin"


@unique
class FloorMissKind(StrEnum):
    """Which soft guarantee (penalty 1000 in E5) was not met."""

    FLOOR = "floor"
    OWN_PLACE_DAY = "own_place_day"
    TAG_MINIMUM = "tag_minimum"


@unique
class VerdictKind(StrEnum):
    """Verdict on one candidate place (HackYeah-TuttiTripTeam/tuttitrip-backend#51)."""

    MUST = "must"
    FITS = "fits"
    ICONIC_NOT_YOURS = "iconic_not_yours"
    SKIP = "skip"


class PlanCreate(BaseModel):
    """Optional knobs for generating a plan."""

    alpha: float = Field(
        default=1.0,
        ge=0,
        le=3,
        description="Fairness slider: 0 utility, 1 Nash, 3 near-egalitarian.",
    )
    weight_preset: str = Field(
        default="default",
        min_length=1,
        max_length=50,
        description="Weight preset name.",
    )


class PlanParams(BaseModel):
    """Parameters the plan was computed with."""

    alpha: float = Field(description="Fairness slider (alpha of phi_alpha, E5).")
    weight_preset: str = Field(description="Weight preset name.")


class PlanItem(BaseModel):
    """One place in a day, in visiting order."""

    place_id: UUID
    name: str
    kind: PlaceKind
    lat: float
    lon: float
    start: dt.time = Field(description="Arrival time.")
    end: dt.time = Field(description="Departure time.")
    transfer_min: int = Field(
        ge=0, description="Minutes of transfer before this place."
    )
    cost_per_person: float = Field(
        ge=0, description="Price per person after inflation, PLN."
    )
    price_base: float = Field(
        ge=0, description="Price per person before inflation by delta, PLN."
    )
    price_inflated: float = Field(
        ge=0,
        description="Price per person after inflating by delta, PLN.",
    )
    price_verified: bool
    price_source_url: str | None = None
    hours_verified: bool
    google_place_id: str | None = Field(
        default=None,
        description="For the Places UI Kit card; no Places data is returned.",
    )


class RequirementState(BaseModel):
    """State of one lodging requirement (E2 lodging)."""

    feature: str
    hard: bool = Field(description="Hard requirements multiply S_h, soft ones average.")
    status: RequirementStatus


class Lodging(BaseModel):
    """The lodging base (one for all nights)."""

    name: str
    lat: float
    lon: float
    nights: int = Field(ge=0)
    cost_total: float = Field(ge=0, description="All nights for the whole group, PLN.")
    s_h: float = Field(ge=0, le=1, description="Lodging contract S_h.")
    requirements: list[RequirementState] = Field(default_factory=list)


class PlanDay(BaseModel):
    """One day of the plan."""

    index: int = Field(ge=1, description="1-based day number.")
    date: dt.date | None = None
    items: list[PlanItem]


class DomainScore(BaseModel):
    """Satisfaction of one person in one domain."""

    domain: Domain
    q: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="q_ij on a 0-100 scale; null when not applicable.",
    )
    not_applicable: bool = Field(
        default=False, description="The person's weight a_ij for this domain is 0."
    )


class PersonFairness(BaseModel):
    """One row of the fairness ledger."""

    profile_id: UUID
    name: str
    u: float = Field(ge=0, le=100, description="Welfare in the group plan.")
    u_star: float = Field(ge=0, le=100, description="Welfare of the best plan alone.")
    r: float = Field(
        ge=0, le=1, description="min(1, (u+10)/(u_star+10)): 'x% of your maximum'."
    )
    floor: float = Field(ge=0, description="Requested floor f_i.")
    floor_eff: float = Field(ge=0, description="min(f_i, 0.6 * u_star).")
    floor_met: bool
    domains: list[DomainScore] = Field(
        description="Exactly five entries, one per domain."
    )
    own_place_days: int = Field(
        ge=0, description="Days with a place of their own (m >= 0.6)."
    )
    weakest_domain: Domain


class Fairness(BaseModel):
    """The fairness measure and ledger."""

    group_size: int = Field(
        ge=1, description="For 1 the UI shows domains instead of Jain."
    )
    jain: float = Field(
        ge=0, le=1, description="Jain index of r; 1 for a single person."
    )
    min_r: float = Field(ge=0, le=1)
    per_person: list[PersonFairness]


class FloorMiss(BaseModel):
    """A soft guarantee that could not be met."""

    kind: FloorMissKind
    profile_id: UUID
    shortfall: float = Field(
        ge=0, description="How much is missing (points, days or places)."
    )
    reason: str = Field(min_length=1)


class Conflict(BaseModel):
    """A conflict between people or constraints; always with a reason."""

    code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    profile_ids: list[UUID] = Field(default_factory=list)
    place_id: UUID | None = None


class ExplainEntry(BaseModel):
    """``explain()``: why this place for this person."""

    place_id: UUID
    profile_id: UUID
    match: float = Field(ge=0, le=1, description="m_ip.")
    effort: float = Field(ge=0, le=1, description="e_ip.")
    utility: float = Field(ge=0, le=100, description="u_ip (E1).")


class VoteReason(BaseModel):
    """A person on one side of a verdict, with a reason code."""

    profile_id: UUID
    reason_code: str | None = Field(default=None, description="E.g. 'za_daleko'.")


class Verdict(BaseModel):
    """Verdict on one candidate place (filled by backend#51)."""

    place_id: UUID
    verdict: VerdictKind
    yes: list[VoteReason] = Field(default_factory=list)
    no: list[VoteReason] = Field(default_factory=list)
    alternative_place_id: UUID | None = None
    explanation: str | None = Field(
        default=None, description="Written later by a model."
    )


class Budget(BaseModel):
    """Cost of the plan against the budget and the approval (E6)."""

    cost: float = Field(ge=0, description="c(P), PLN.")
    b_from: float = Field(ge=0, description="B_od.")
    b_to: float = Field(ge=0, description="B_do.")
    b_max: float = Field(ge=0, description="B_max (hard).")
    zone: BudgetZone
    over_budget: float = Field(ge=0, description="PLN above B_do (0 when within).")
    needs_approval: bool = Field(
        description="The organizer must approve going over B_do."
    )
    kappa: float | None = Field(
        default=None,
        description="Price per point in PLN/pt; set only with needs_approval.",
    )


class Telemetry(BaseModel):
    """How the plan was computed."""

    solver: str
    steps: int = Field(ge=0)
    solo_runs: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)


class PlanRead(BaseModel):
    """A plan with the fairness measure, ledger, verdicts and budget."""

    id: UUID
    trip_id: UUID
    version: int = Field(ge=1)
    input_hash: str
    plan_hash: str = Field(
        min_length=12,
        max_length=12,
        description="SHA-256 prefix, 12 characters, reproducible.",
    )
    created_at: dt.datetime
    params: PlanParams
    days: list[PlanDay]
    lodging: Lodging | None = Field(
        default=None, description="Null for a one-day trip."
    )
    fairness: Fairness
    floors_missed: list[FloorMiss] = Field(default_factory=list)
    violation: float = Field(ge=0, description="V(P) of E5; 0 when nothing is missed.")
    conflicts: list[Conflict] = Field(default_factory=list)
    explain: list[ExplainEntry] = Field(default_factory=list)
    verdicts: list[Verdict] | None = Field(
        default=None, description="Null until backend#51; then one per candidate."
    )
    budget: Budget
    telemetry: Telemetry
