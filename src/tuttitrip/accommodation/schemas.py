"""Accommodation DTOs."""

from datetime import date
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from tuttitrip.accommodation.logic.keys import Platform, RequirementKind, is_known_key
from tuttitrip.accommodation.logic.search_links import PriceBasis
from tuttitrip.shared.jobs.contracts import ProviderName
from tuttitrip.shared.pagination.schemas import ListFilters, PageParams, SortDir

LABELS: dict[str, str] = {
    "pool": "pool",
    "kitchen": "kitchen",
    "parking": "parking",
    "family_room": "family room",
    "wifi": "Wi-Fi",
    "air_conditioning": "air conditioning",
    "breakfast": "breakfast",
    "pets_allowed": "pets allowed",
    "elevator": "elevator",
    "wheelchair_accessible": "wheelchair access",
    "washing_machine": "washing machine",
    "balcony": "balcony",
    "crib": "crib",
    "playground": "playground",
    "airbnb": "Airbnb",
    "booking": "Booking.com",
    "attractions": "distance to attractions",
}
"""Readable English name of every requirement key, for plan-check messages and
for the worker's model (``RequirementLabel``); the client translates keys itself."""


def requirement_label(key: str) -> str:
    """Readable name of a requirement key.

    Args:
        key: Requirement key (amenity, platform or distance).

    Returns:
        The label, or the key with spaces for one outside the dictionary.
    """
    return LABELS.get(key, key.replace("_", " "))


class RequirementStatus(StrEnum):
    """Whether an offer satisfies a requirement."""

    MET = "met"
    UNMET = "unmet"
    UNCONFIRMED = "unconfirmed"


class UnconfirmedReason(StrEnum):
    """Why a requirement is ``unconfirmed`` (the UI label is in parentheses).

    * ``no_mention``: the offer says nothing about it
      ("brak wzmianki w ofercie");
    * ``low_confidence``: a quote exists, but the assessment is below the
      confidence threshold ("niepewna ocena");
    * ``not_assessed``: a quote exists, but no model could assess it
      ("cytat bez oceny");
    * ``conflicting``: confident quotes say both yes and no ("sprzeczne cytaty");
    * ``no_link``: a platform requirement and the offer has no link ("brak linku");
    * ``not_checked``: nothing in a pasted offer can tell (distance), or the
      requirement was added after the check ("nie sprawdzono");
    * ``pending``: the check is still running ("sprawdzanie trwa");
    * ``check_failed``: the check failed ("sprawdzenie nie powiodło się");
    * ``no_offer``: a night without any offer, only in the plan check ("brak oferty").
    """

    NO_MENTION = "no_mention"
    LOW_CONFIDENCE = "low_confidence"
    NOT_ASSESSED = "not_assessed"
    CONFLICTING = "conflicting"
    NO_LINK = "no_link"
    NOT_CHECKED = "not_checked"
    PENDING = "pending"
    CHECK_FAILED = "check_failed"
    NO_OFFER = "no_offer"


class Requirement(BaseModel):
    """A requirement as the contract checks it, e.g. a hard ``pool``."""

    feature: str = Field(min_length=1)
    kind: RequirementKind = RequirementKind.AMENITY
    hard: bool = True


class OfferFeatures(BaseModel):
    """What we know about an offer: confirmed present or confirmed absent.

    Typed by the host (for example when no model is available); a key listed
    here is decided by the host and not sent to the worker.
    """

    present: set[str] = Field(default_factory=set, max_length=60)
    absent: set[str] = Field(default_factory=set, max_length=60)

    @model_validator(mode="after")
    def _disjoint(self) -> Self:
        if self.present & self.absent:
            msg = "a feature cannot be both present and absent"
            raise ValueError(msg)
        return self


class RequirementCheck(BaseModel):
    """Result for one requirement: ``met`` and ``unmet`` come with a quote.

    The quote is a verbatim span of the pasted offer (or the link's domain for a
    platform requirement). It is null only when the host typed the answer in
    ``features``. ``unconfirmed`` always has a ``reason`` and may carry the
    quote that was not convincing.
    """

    feature: str
    kind: RequirementKind = RequirementKind.AMENITY
    hard: bool = True
    status: RequirementStatus
    quote: str | None = None
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "Decision model's margin from its threshold, scaled to 0..1; not a "
            "probability. Null for host answers, links and missing assessments."
        ),
    )
    reason: UnconfirmedReason | None = Field(
        default=None, description="Set exactly when `status` is `unconfirmed`."
    )


class RequirementItem(BaseModel):
    """One lodging requirement of a trip (a switch the host turned on).

    ``hard`` requirements multiply into the lodging score and ``S_h`` of E2
    (unmet means 0); soft ones are averaged. A requirement applies to the one
    lodging base for the whole trip (docs/algorytm.md, section 9).
    """

    model_config = ConfigDict(from_attributes=True)

    kind: RequirementKind
    key: str = Field(min_length=1, max_length=64)
    hard: bool
    max_distance_m: int | None = Field(
        default=None, gt=0, description="Required for `distance`, forbidden otherwise."
    )

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not is_known_key(self.kind, self.key):
            msg = f"Unknown {self.kind} key '{self.key}'"
            raise ValueError(msg)
        if (self.kind is RequirementKind.DISTANCE) != (self.max_distance_m is not None):
            msg = "max_distance_m is required for distance and only for distance"
            raise ValueError(msg)
        return self


class RequirementsWrite(BaseModel):
    """PUT payload: the whole set of requirements replaces the stored one."""

    requirements: list[RequirementItem] = Field(default_factory=list, max_length=60)

    @model_validator(mode="after")
    def _check(self) -> Self:
        pairs = [(item.kind, item.key) for item in self.requirements]
        if len(set(pairs)) != len(pairs):
            msg = "each (kind, key) may appear only once"
            raise ValueError(msg)
        return self


class RequirementsRead(RequirementsWrite):
    """The requirements of a trip with their version."""

    version: int = Field(
        description=(
            "Counter of changes. An offer check stores the version it used; a "
            "different current version makes that result stale."
        )
    )


class SearchLinkParam(BaseModel):
    """One query parameter of a search link."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    value: str
    official: bool = Field(
        description=(
            "False when the platform does not document the parameter, so it may "
            "stop working; the card should say so."
        )
    )


class SearchLinkRead(BaseModel):
    """A search link to one platform."""

    platform: Platform
    url: str = Field(description="Search with all filters; opened by a person.")
    fallback_url: str = Field(
        description="Same place without filters, for when a parameter stops working."
    )
    params: list[SearchLinkParam]


class NightlyPrice(BaseModel):
    """Upper price filter per night and where it came from.

    Not a lodging price or an accommodation budget: it is the group's whole
    daily budget used as a ceiling. Label it "group daily limit" in the UI.
    """

    amount: int = Field(description="Whole units of `currency`, rounded down.")
    currency: str = Field(description="ISO 4217.")
    basis: PriceBasis = Field(
        description=(
            "`budget_day_max`: the trip's daily limit. "
            "`budget_total_max_per_night`: the total limit divided by the nights. "
            "Both are the group's whole budget for everything, used only as a "
            "ceiling for the search, never as the price of a night."
        )
    )


class SearchLinksRead(BaseModel):
    """What a host sees on the approval card before opening a platform."""

    check_in: date
    check_out: date
    nights: int
    adults: int
    child_ages: list[int] = Field(description="Ages of the people under 18.")
    area: str | None = Field(description="City or destination; null when unknown.")
    price_per_night: NightlyPrice | None = Field(
        description=(
            "Group daily limit used as a search ceiling (not a night price); "
            "null when the trip has no budget or no currency is known."
        )
    )
    platforms_restricted: bool = Field(
        description="True when a hard platform requirement removed some platforms."
    )
    requirements_version: int
    links: list[SearchLinkRead]


class SearchOpenWrite(BaseModel):
    """The host approved opening this platform's search."""

    platform: Platform


class SearchOpeningRead(BaseModel):
    """One approved opening in the append-only log."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    platform: Platform
    url: str
    params: list[SearchLinkParam]
    actor_sub: str = Field(description="Who approved it (Auth0 `sub`).")
    opened_at: AwareDatetime


class OpeningSort(StrEnum):
    """Sort keys of the openings log."""

    OPENED_AT = "opened_at"


class OpeningFilters(ListFilters):
    """Filters of the openings log."""

    platform: Platform | None = None


class OpeningQuery(PageParams, OpeningFilters):
    """Query of ``GET .../search-links/opened``."""

    sort: OpeningSort = OpeningSort.OPENED_AT
    dir: SortDir = SortDir.DESC


MAX_OFFER_NIGHTS = 60


class OfferCheckState(StrEnum):
    """Where the check of an offer is."""

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class OfferCreate(BaseModel):
    """A pasted lodging offer to check for some nights of the trip."""

    document_id: UUID = Field(
        description="Id of the pasted text (`POST .../documents`, kind `offer`)."
    )
    nights: list[date] = Field(
        min_length=1,
        max_length=MAX_OFFER_NIGHTS,
        description="Dates of the nights (check-in date of each night).",
    )
    url: str | None = Field(
        default=None,
        max_length=2000,
        pattern=r"^https?://[^\s/]+",
        description="Link to the offer; its domain decides platform requirements.",
    )
    features: OfferFeatures = Field(
        default_factory=OfferFeatures,
        description="Amenities the host confirmed by hand; these skip the model.",
    )
    provider: ProviderName = "openrouter"

    @model_validator(mode="after")
    def _unique_nights(self) -> Self:
        if len(set(self.nights)) != len(self.nights):
            msg = "each night may appear only once"
            raise ValueError(msg)
        self.nights = sorted(self.nights)
        return self


class OfferRead(BaseModel):
    """A pasted offer and its three-state check against the current requirements."""

    id: UUID
    trip_id: UUID
    document_id: UUID
    nights: list[date]
    url: str | None
    platform: Platform | None = Field(
        description="Platform from the link's domain; null without a known one."
    )
    state: OfferCheckState
    job_id: str | None = Field(
        description="Worker job (`GET /jobs/{id}`); null when no model was needed."
    )
    error_code: str | None = Field(
        default=None, description="Worker error code when `state` is `failed`."
    )
    requirements_version: int = Field(description="Version the check was started with.")
    stale: bool = Field(
        description=(
            "True when the requirements changed since; requirements added "
            "later are `unconfirmed` (`not_checked`)."
        )
    )
    checks: list[RequirementCheck]
    score: float = Field(
        ge=0,
        le=1,
        description=(
            "S_h of E2: product over hard requirements times the mean over soft "
            "ones; met 1, unconfirmed 0.4, unmet 0."
        ),
    )
    created_at: AwareDatetime
