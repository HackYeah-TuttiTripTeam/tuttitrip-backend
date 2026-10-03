"""Preference DTOs: interests, importance pool, constraints, diet, examples, minima.

The taxonomies are not copied: interests use ``PlaceTag``, diets ``DietTag`` and
cuisine minima ``Cuisine`` from the places catalog, so what a person says and
what a place offers are compared code for code (docs/algorytm.md, E1 and E5).
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from tuttitrip.places.schemas import Cuisine, DietTag, PlaceTag

POOL_TOTAL = 10

Interest = Annotated[float, Field(ge=0, le=1)]
PoolPoints = Annotated[int, Field(ge=0, le=POOL_TOTAL)]


class ImportanceDomain(StrEnum):
    """The five domains a person spreads their importance over."""

    LODGING = "lodging"
    FOOD = "food"
    ATTRACTIONS = "attractions"
    PACE = "pace"
    COST = "cost"


class ImportancePool(BaseModel):
    """Ten points over the five domains (``a_ij`` of the spec, before ``/10``)."""

    lodging: PoolPoints
    food: PoolPoints
    attractions: PoolPoints
    pace: PoolPoints
    cost: PoolPoints

    @model_validator(mode="after")
    def _sums_to_ten(self) -> Self:
        total = sum(self.model_dump().values())
        if total != POOL_TOTAL:
            msg = (
                f"The importance pool must add up to exactly {POOL_TOTAL} points"
                f" (got {total})"
            )
            raise ValueError(msg)
        return self


class Constraints(BaseModel):
    """Health and access limits.

    Heat, cold and audio description are stored only: without weather data they
    reach the plan through the places' ``indoor`` flag, not on their own.
    """

    wheelchair: bool = False
    stairs: bool = False
    heat: bool = False
    cold: bool = False
    audio_description: bool = False
    disability_note: str | None = Field(default=None, max_length=500)


class Diet(BaseModel):
    """Diets (codes shared with the catalog's ``diet_tags``) and free-text allergies."""

    tags: list[DietTag] = Field(default_factory=list, max_length=len(DietTag))
    allergies: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(
        default_factory=list, max_length=20
    )

    @model_validator(mode="after")
    def _no_duplicate_tags(self) -> Self:
        if len(self.tags) != len(set(self.tags)):
            msg = "Each diet tag may appear only once"
            raise ValueError(msg)
        return self


class ExampleVerdict(StrEnum):
    """What the person thinks of an example place."""

    LIKE = "like"
    DISLIKE = "dislike"


class ExamplePlace(BaseModel):
    """A place the person likes or dislikes; ``place_id`` is set for catalog places."""

    name: str = Field(min_length=1, max_length=200)
    place_id: UUID | None = None
    verdict: ExampleVerdict


class MinTagDomain(StrEnum):
    """Domain a minimum tag belongs to (the pool domain whose points trigger it)."""

    FOOD = "food"
    ATTRACTIONS = "attractions"


class MinTag(BaseModel):
    """A tag the person wants at least once, e.g. food ``indian``.

    ``tag`` is a ``Cuisine`` for ``food`` and a ``PlaceTag`` for ``attractions``.
    The solver turns the domain's points into the number of places (E5).
    """

    domain: MinTagDomain
    tag: str

    @model_validator(mode="after")
    def _tag_belongs_to_domain(self) -> Self:
        taxonomy = Cuisine if self.domain is MinTagDomain.FOOD else PlaceTag
        if self.tag not in {t.value for t in taxonomy}:
            msg = f"'{self.tag}' is not a {taxonomy.__name__} value"
            raise ValueError(msg)
        return self


class _PreferenceFields(BaseModel):
    interests: dict[PlaceTag, Interest] = Field(
        default_factory=dict, description="Interest profile I_i: tag to strength 0..1."
    )
    constraints: Constraints = Field(default_factory=Constraints)
    diet: Diet = Field(default_factory=Diet)
    example_places: list[ExamplePlace] = Field(default_factory=list, max_length=50)
    min_tags: list[MinTag] = Field(default_factory=list, max_length=30)


class PreferencesWrite(_PreferenceFields):
    """The whole preferences of one person (PUT replaces them)."""

    importance_pool: ImportancePool | None = Field(
        default=None, description="Omitted: the default for the person's age group."
    )


class PreferencesRead(_PreferenceFields):
    """Preferences of one person."""

    profile_id: UUID
    importance_pool: ImportancePool
    filled: bool = Field(
        description="False while nobody has saved them: the pool is the age default."
    )
    updated_by_sub: str | None
    updated_at: datetime | None
