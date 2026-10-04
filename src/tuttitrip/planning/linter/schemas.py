"""Linter DTOs."""

from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from tuttitrip.accommodation.schemas import RequirementCheck, RequirementItem
from tuttitrip.places.schemas import PlaceRead


class LintItem(BaseModel):
    """One stop of a day: a catalog place or an unrecognised name."""

    name: str = Field(min_length=1)
    place_id: UUID | None = Field(
        default=None, description="Null when the stop was not recognised."
    )
    start: time = Field(description="Local arrival time.")
    end: time | None = Field(
        default=None, description="Local end; null means start + the typical visit."
    )
    cost: Decimal = Field(
        default=Decimal(0), ge=0, description="Total for the group, as in the plan."
    )
    price_verified: bool = Field(
        default=True,
        description="False inflates the cost by delta (E6). Pasted plans: true.",
    )

    @model_validator(mode="after")
    def _ends_after_start(self) -> Self:
        if self.end is not None and self.end <= self.start:
            msg = "end must be later than start"
            raise ValueError(msg)
        return self


class LintDay(BaseModel):
    """The stops of one local date."""

    day: date
    items: list[LintItem]


class LintPlan(BaseModel):
    """A plan to check: days with stops, as typed or pasted."""

    days: list[LintDay]


class LintPerson(BaseModel):
    """One participant as the person rules see them (profile plus preferences).

    Everybody takes part in every stop (docs/algorytm.md, section 9).
    """

    id: UUID
    name: str = Field(min_length=1)
    segment_km: float = Field(gt=0, description="s_i: longest walk in one go.")
    daily_km: float = Field(gt=0, description="D_i: daily walking distance.")
    nap_start: time | None = Field(default=None, description="Local start of the nap.")
    nap_minutes: int = Field(default=0, ge=0, le=600)
    stairs_sensitivity: float = Field(
        default=0,
        ge=0,
        le=1,
        description="Effective sensitivity: 1 with the stairs or wheelchair limit.",
    )
    wheelchair: bool = False


class LintLunch(BaseModel):
    """Lunch the group needs: a free gap that starts in ``[earliest, latest]``."""

    earliest: time
    latest: time
    minutes: int = Field(gt=0, le=240)


class LintOffer(BaseModel):
    """A checked lodging offer: copy ``nights`` and ``checks`` from ``OfferRead``."""

    id: UUID | None = None
    nights: list[date] = Field(min_length=1)
    checks: list[RequirementCheck]


class LintLodging(BaseModel):
    """Nights of the trip, its lodging requirements and the checked offers."""

    nights: list[date] = Field(description="Every night that needs a bed.")
    requirements: list[RequirementItem] = Field(
        description="As in `GET .../accommodation/requirements`; only hard ones count."
    )
    offers: list[LintOffer] = Field(default_factory=list)


class LintContext(BaseModel):
    """What the rules compare a plan with."""

    places: list[PlaceRead] = Field(description="Catalog places the plan may use.")
    people: list[LintPerson] = Field(
        default_factory=list, description="Participants; person rules need them."
    )
    lunch: LintLunch | None = Field(
        default=None, description="Lunch window; null disables the lunch check."
    )
    timezone: str = Field(description="IANA zone of the city; hours are local.")
    lodging: LintLodging | None = Field(
        default=None,
        description="Lodging offers per night; null disables the lodging check.",
    )
    budget: Decimal = Field(ge=0, description="B_do.")
    flex_pct: int = Field(default=0, ge=0, le=50, description="Margin of B_max.")

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"unknown time zone {value!r}"
            raise ValueError(msg) from exc
        return value

    @property
    def b_max(self) -> Decimal:
        """Hard budget limit ``B_do * (1 + flex)`` (E0)."""
        return self.budget * (100 + self.flex_pct) / 100


class LintRequest(BaseModel):
    """A plan and its context."""

    plan: LintPlan
    context: LintContext


class Severity(StrEnum):
    """A violation counts in the score; a warning only informs."""

    VIOLATION = "violation"
    WARNING = "warning"


class Finding(BaseModel):
    """One violation or warning of a rule, with its position in the plan."""

    rule: str
    severity: Severity
    message: str
    day: date | None = None
    position: int | None = Field(
        default=None, description="Index of the stop in the day as sent."
    )
    place_name: str | None = None
    person_id: UUID | None = Field(
        default=None, description="Set when one person is to blame."
    )
    person_name: str | None = None


class RuleResult(BaseModel):
    """The outcome of one rule; present even with zero violations."""

    rule: str
    weight: int
    count: int
    violations: list[Finding]
    warnings: list[Finding]


class LintReport(BaseModel):
    """Every rule in a fixed order, the weighted score and a stable digest."""

    results: list[RuleResult]
    count: int = Field(description="Violations of all rules.")
    score: int = Field(description="Sum of weight * count; 0 means a clean plan.")
    digest: str = Field(description="First 12 hex of the SHA-256 of the results.")


MAX_DOCUMENT_CHARS = 20_000


class DocumentKind(StrEnum):
    """What a pasted text is: a plan from another tool or a lodging offer."""

    PLAN = "plan"
    OFFER = "offer"


class DocumentCreate(BaseModel):
    """Text pasted by the host (stored as typed, deleted with the trip)."""

    kind: DocumentKind
    text: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_DOCUMENT_CHARS
        ),
    ]


class DocumentRead(BaseModel):
    """A stored pasted text (not echoed back; the worker reads it by ``id``)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trip_id: UUID
    kind: DocumentKind
    created_by: str
    created_at: datetime
