"""Accommodation DTOs."""

from datetime import date
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from tuttitrip.accommodation.logic.keys import Platform, RequirementKind, is_known_key
from tuttitrip.accommodation.logic.search_links import PriceBasis
from tuttitrip.shared.pagination.schemas import ListFilters, PageParams, SortDir


class RequirementStatus(StrEnum):
    """Whether an offer satisfies a requirement."""

    MET = "met"
    UNMET = "unmet"
    UNCONFIRMED = "unconfirmed"


class Requirement(BaseModel):
    """A must-have feature, e.g. ``pool``."""

    feature: str = Field(min_length=1)


class OfferFeatures(BaseModel):
    """What we know about an offer: confirmed present or confirmed absent."""

    present: set[str] = Field(default_factory=set)
    absent: set[str] = Field(default_factory=set)


class RequirementCheck(BaseModel):
    """Result for one requirement."""

    feature: str
    status: RequirementStatus


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
