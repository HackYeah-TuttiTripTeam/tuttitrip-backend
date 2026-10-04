"""Plan DTOs: the response shape of ``docs/algorytm.md``, section 10.

Symbols follow the specification: ``u`` (welfare), ``u_star`` (best alone),
``r`` (share of it), ``q`` (domain satisfaction), ``S_h`` (lodging contract),
``kappa`` (price per point). Money is ``Decimal`` (serialised as a string) in
the trip currency. Fields filled by later issues are optional or empty, so
their arrival does not change the shape. Generic names carry a ``Plan`` prefix
so they never collide with other domains in the OpenAPI schema.
"""

import datetime as dt
from decimal import Decimal
from enum import StrEnum, unique
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from tuttitrip.accommodation.schemas import RequirementStatus
from tuttitrip.profiles.feedback.schemas import ReasonCode

Money = Annotated[Decimal, Field(ge=0, decimal_places=2, max_digits=12)]
Hash12 = Annotated[str, Field(min_length=12, max_length=12)]
MAX_ASSUMED_PEOPLE = 10
"""Most people a draft plan may assume."""


@unique
class Currency(StrEnum):
    """Trip currency."""

    PLN = "PLN"
    EUR = "EUR"
    GBP = "GBP"


@unique
class WeightPreset(StrEnum):
    """Weight preset of the fairness solver."""

    DEFAULT = "default"
    EQUAL = "equal"
    WEIGHTED = "weighted"


@unique
class PlanDomainCode(StrEnum):
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
class TransferMode(StrEnum):
    """How people get to a stop."""

    WALK = "walk"
    TRANSIT = "transit"
    CAR = "car"
    BIKE = "bike"


@unique
class BudgetZone(StrEnum):
    """Where the plan cost falls against the budget (E2 cost, E6)."""

    BELOW_B_FROM = "below_b_from"
    UP_TO_B_TO = "up_to_b_to"
    IN_MARGIN = "in_margin"


@unique
class ApprovalStatus(StrEnum):
    """State of the organizer's approval of going over ``B_do``."""

    NOT_NEEDED = "not_needed"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@unique
class FloorMissKind(StrEnum):
    """Which soft guarantee (penalty 1000 in E5) was not met; the code."""

    FLOOR = "floor"
    OWN_PLACE_DAY = "own_place_day"
    TAG_MINIMUM = "tag_minimum"


@unique
class ConflictCode(StrEnum):
    """Why a conflict is reported; the UI writes the text (PL/EN)."""

    LODGING_HARD_REQUIREMENT = "lodging_hard_requirement"
    VETO_BLOCKS_PLACE = "veto_blocks_place"
    BUDGET_LIMIT = "budget_limit"
    FLOOR_UNREACHABLE = "floor_unreachable"
    UNKNOWN_PRICE = "unknown_price"
    OTHER = "other"


@unique
class VerdictKind(StrEnum):
    """Verdict on one candidate place (HackYeah-TuttiTripTeam/tuttitrip-backend#51)."""

    MUST = "must"
    FITS = "fits"
    ICONIC_NOT_YOURS = "iconic_not_yours"
    SKIP = "skip"


class PlanCreate(BaseModel):
    """Optional knobs for generating a plan."""

    alpha: float | None = Field(
        default=None,
        ge=0,
        le=3,
        description=(
            "Fairness slider: 0 utility, 1 Nash, 3 near-egalitarian. Omitted: the "
            "trip's own `fairness_alpha`."
        ),
    )
    weight_preset: WeightPreset = Field(
        default=WeightPreset.DEFAULT,
        description=(
            "Recorded with the plan and part of its input hash, but it has no effect "
            "on the computation yet: the weights come from the profiles "
            "(`PUT /trips/{id}/profiles/weights`)."
        ),
    )


class PlanAssumptions(BaseModel):
    """What a draft plan fills in where the trip has no data yet.

    Used while the interview is still going: dates the trip lacks and people it
    lacks are assumed in memory (nothing is stored on the trip), and the version
    is marked ``draft``. A date or a person the trip has is never replaced.
    """

    start_date: dt.date | None = Field(
        default=None, description="Assumed first day, used only if the trip has none."
    )
    end_date: dt.date | None = Field(
        default=None, description="Assumed last day, used only if the trip has none."
    )
    min_people: int = Field(
        default=0,
        ge=0,
        le=MAX_ASSUMED_PEOPLE,
        description="The group is filled up to this size with assumed adults.",
    )

    @model_validator(mode="after")
    def _dates_in_order(self) -> Self:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            msg = "end_date must not be before start_date"
            raise ValueError(msg)
        return self


class PlanParams(BaseModel):
    """Parameters the plan was computed with."""

    alpha: float = Field(description="Fairness slider (alpha of phi_alpha, E5).")
    weight_preset: WeightPreset
    draft: bool = Field(
        default=False,
        description="A preliminary plan made with assumptions during the interview.",
    )
    parameters_version: int = Field(
        default=0,
        ge=0,
        description="Version of the admin parameters (0: the built-in defaults).",
    )


class StopTransfer(BaseModel):
    """The leg to a stop from the previous one."""

    minutes: int = Field(ge=0)
    mode: TransferMode
    cost: Money | None = Field(default=None, description="Null: no data.")


class PlanStop(BaseModel):
    """One place in a day, in visiting order."""

    place_id: UUID
    name: str
    kind: PlaceKind
    address: str | None = Field(
        default=None, description="Street address of the place; null: no data."
    )
    lat: float
    lon: float
    start: dt.time = Field(description="Arrival time.")
    end: dt.time = Field(description="Departure time.")
    transfer: StopTransfer | None = Field(
        default=None, description="Leg to this stop; null for the first of the day."
    )
    cost_per_person: Money | None = Field(
        default=None, description="Price per person after inflation; null: no data."
    )
    price_base: Money | None = Field(
        default=None, description="Price per person before inflating by delta."
    )
    price_inflated: Money | None = Field(
        default=None, description="Price per person after inflating by delta."
    )
    price_verified: bool
    price_source_url: str | None = None
    price_verified_at: dt.datetime | None = None
    hours_verified: bool
    hours_source_url: str | None = None
    hours_verified_at: dt.datetime | None = None
    google_place_id: str | None = Field(
        default=None,
        description="For the Places UI Kit card; no Places data is returned.",
    )


class RequirementState(BaseModel):
    """State of one lodging requirement (E2 lodging)."""

    feature: str
    hard: bool = Field(description="Hard requirements multiply S_h, soft ones average.")
    status: RequirementStatus


class PlanLodging(BaseModel):
    """The lodging base (one for all nights)."""

    name: str
    lat: float
    lon: float
    nights: int = Field(ge=0)
    cost_total: Money | None = Field(
        default=None, description="All nights for the whole group; null: no data."
    )
    s_h: float = Field(ge=0, le=1, description="Lodging contract S_h.")
    requirements: list[RequirementState] = Field(default_factory=list)


class PlanDay(BaseModel):
    """One day of the plan."""

    index: int = Field(ge=1, description="1-based day number.")
    date: dt.date | None = None
    items: list[PlanStop]


class PlanDomainScore(BaseModel):
    """Satisfaction of one person in one domain."""

    domain: PlanDomainCode
    q: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="q_ij on a 0-100 scale; null exactly when not applicable.",
    )
    not_applicable: bool = Field(
        default=False, description="The person's weight a_ij for this domain is 0."
    )

    @model_validator(mode="after")
    def _q_matches_applicability(self) -> Self:
        if (self.q is None) != self.not_applicable:
            msg = "q is None if and only if not_applicable"
            raise ValueError(msg)
        return self


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
    domains: list[PlanDomainScore] = Field(
        min_length=5, max_length=5, description="Exactly five entries, one per domain."
    )
    own_place_days: int = Field(
        ge=0, description="Days with a place of their own (m >= 0.6)."
    )
    weakest_domain: PlanDomainCode | None = Field(
        description="The applicable domain with the lowest q; null if none applies."
    )


class PlanFairness(BaseModel):
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
    """A soft guarantee that could not be met; ``kind`` is the code."""

    kind: FloorMissKind
    profile_id: UUID
    shortfall: float = Field(
        ge=0, description="How much is missing (points, days or places)."
    )
    day: int | None = Field(default=None, ge=1, description="Day, for own_place_day.")
    tag: str | None = Field(default=None, description="Tag, for tag_minimum.")
    params: dict[str, str | int | float] = Field(default_factory=dict)


class PlanConflict(BaseModel):
    """A conflict between people or constraints; always with a reason code."""

    reason_code: ConflictCode
    params: dict[str, str | int | float] = Field(default_factory=dict)
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
    reason_code: ReasonCode | None = None


class PlanVerdict(BaseModel):
    """Verdict on one candidate place (filled by backend#51)."""

    place_id: UUID
    verdict: VerdictKind
    v_p: float | None = Field(
        default=None,
        ge=-1,
        le=1,
        description="Weighted opinion V_p (extension, outside v1.0).",
    )
    yes: list[VoteReason] = Field(default_factory=list)
    no: list[VoteReason] = Field(default_factory=list)
    skip_codes: list[str] = Field(
        default_factory=list,
        description=(
            "E0 codes of a skip: veto, blocked, closed, no_fit, segment, stairs."
        ),
    )
    substitute_place_id: UUID | None = None
    explanation: str | None = Field(
        default=None, description="Written later by a model."
    )


class PlanBudget(BaseModel):
    """Plan cost against the budget and the approval (E6); money in ``currency``."""

    currency: Currency
    cost: Money = Field(description="c(P).")
    b_from: Money = Field(description="B_od.")
    b_to: Money | None = Field(description="B_do; null for a trip without a budget.")
    b_max: Money | None = Field(
        description="B_max (hard); null for a trip without a budget."
    )
    unlimited: bool = Field(
        default=False,
        description="The trip has no budget: nothing limits the cost, q_cost is n/a.",
    )
    zone: BudgetZone
    over_budget: Money = Field(description="Amount above B_do (0 when within).")
    needs_approval: bool = Field(
        description="The organizer must approve going over B_do."
    )
    kappa: Decimal | None = Field(
        default=None,
        ge=0,
        decimal_places=2,
        description="Price per point (currency per pt); set iff needs_approval.",
    )
    gain_profile_id: UUID | None = Field(
        default=None, description="Who gains most from going over."
    )
    gain_points: float | None = Field(
        default=None, ge=0, description="Their gain in points."
    )
    strict_plan_id: UUID | None = Field(
        default=None, description="The P_strict alternative."
    )
    strict_cost: Money | None = None
    approval_status: ApprovalStatus = ApprovalStatus.NOT_NEEDED

    @model_validator(mode="after")
    def _kappa_matches_approval(self) -> Self:
        if (self.kappa is None) == self.needs_approval:
            msg = "kappa is set if and only if needs_approval"
            raise ValueError(msg)
        return self


class PlanTelemetry(BaseModel):
    """How the plan was computed."""

    solver: str = Field(description="Solver name and version.")
    steps: int = Field(ge=0)
    solo_runs: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)


class PlanRead(BaseModel):
    """A stored plan version with the fairness measure, ledger and budget.

    The content is a copy made when the plan was computed. ``verdicts`` stay
    null until backend#51 fills them; ``lodging`` is null until a lodging base
    can be chosen (backend#70), and then the lodging domain of every person is
    "not applicable". ``budget.needs_approval`` is set by backend#53; places
    without a price are reported as ``unknown_price`` conflicts.
    """

    id: UUID
    trip_id: UUID
    version: int = Field(ge=1)
    input_hash: str = Field(
        min_length=64, max_length=64, description="SHA-256 hex of the input."
    )
    plan_hash: Hash12 = Field(
        description="SHA-256 prefix, 12 characters, reproducible."
    )
    created_at: dt.datetime
    params: PlanParams
    days: list[PlanDay]
    lodging: PlanLodging | None = Field(
        default=None, description="Null for a one-day trip."
    )
    fairness: PlanFairness
    floors_missed: list[FloorMiss] = Field(default_factory=list)
    violation: float = Field(ge=0, description="V(P) of E5; 0 when nothing is missed.")
    conflicts: list[PlanConflict] = Field(default_factory=list)
    explain: list[ExplainEntry] = Field(default_factory=list)
    verdicts: list[PlanVerdict] | None = Field(
        default=None, description="Null until backend#51; then one per candidate."
    )
    budget: PlanBudget
    telemetry: PlanTelemetry
