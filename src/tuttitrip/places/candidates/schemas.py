"""DTOs of the candidate fetch."""

from enum import StrEnum

from pydantic import BaseModel, Field


class CandidatesState(StrEnum):
    """Where the catalog of the trip's city stands."""

    READY = "ready"
    RUNNING = "running"
    FAILED = "failed"
    EMPTY = "empty"


class CandidatesRequest(BaseModel):
    """Optional name for a city the catalog does not know yet."""

    city_query: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "City name for the geocoder, e.g. `Gdańsk, Polska`. Needed only for a "
            "city that was never fetched; it must give the trip's `city_slug` "
            "(lowercase, no diacritics, `-` between words). Omitted: the slug's words."
        ),
    )


class CandidatesStatus(BaseModel):
    """The catalog of the trip's city and the state of its fetch."""

    city_slug: str
    place_count: int = Field(ge=0, description="Places in the catalog of the city.")
    state: CandidatesState = Field(
        description=(
            "`ready`: the city has places (the demo cities never need a job). "
            "`running`: the job is queued or running. `failed`: it ended with "
            "`error_code`. `empty`: no places and no job."
        )
    )
    job_id: str | None = None
    error_code: str | None = Field(
        default=None, description="Worker code: `city_not_found`, `rate_limited`..."
    )
    error: str | None = None
