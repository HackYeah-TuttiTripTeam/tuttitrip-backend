"""DTOs of the city search."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

from tuttitrip.shared.pagination.schemas import Page, PageParams

MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 100


class CityLang(StrEnum):
    """Language of the suggestions (the UI language)."""

    PL = "pl"
    EN = "en"


class CitySource(StrEnum):
    """Where a suggestion comes from."""

    CATALOG = "catalog"
    GEOCODER = "geocoder"


class CitySearchQuery(PageParams):
    """Query of ``GET /places/cities/search``; ``dir`` is accepted and unused."""

    q: Annotated[
        str,
        Field(
            min_length=MIN_QUERY_LENGTH,
            max_length=MAX_QUERY_LENGTH,
            description="What the user typed so far (at least 2 characters).",
        ),
    ]
    lang: Annotated[
        CityLang,
        Field(description="UI language: names of geocoder suggestions follow it."),
    ] = CityLang.PL
    size: Annotated[int, Field(ge=1, le=20, description="Suggestions per page.")] = 8


class CitySuggestion(BaseModel):
    """One city the user can pick."""

    slug: str = Field(
        description=(
            "Value for the trip's `city_slug`. A geocoder suggestion's slug is "
            "`slugify(city_query)`."
        )
    )
    name: str
    country: str = Field(description="ISO 3166-1 alpha-2.")
    region: str | None = Field(default=None, description="State or voivodeship.")
    source: CitySource
    catalog_ready: bool = Field(
        description="In the catalog with places: planning needs no fetch."
    )
    city_query: str | None = Field(
        default=None,
        description=(
            "Geocoder suggestions only: send it as `city_query` to "
            "`POST /trips/{id}/places/candidates` so the places get fetched."
        ),
    )
    center_lat: float
    center_lon: float


class CitySuggestionPage(Page[CitySuggestion]):
    """A page of suggestions, catalog first."""

    geocoder_available: bool = Field(
        description=(
            "False when the geocoder was down, slow or over its request budget: "
            "only catalog cities are listed."
        )
    )
