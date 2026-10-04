"""Location DTOs."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tuttitrip.shared.pagination.schemas import ListFilters, PageParams, SortDir

MIN_SHARING_MINUTES = 5
DEFAULT_SHARING_MINUTES = 240
MAX_SHARING_MINUTES = 1440


class ConsentUpdate(BaseModel):
    """Turn sharing on for a limited time; send it again to extend it."""

    model_config = ConfigDict(extra="forbid")

    duration_minutes: Annotated[
        int,
        Field(
            ge=MIN_SHARING_MINUTES,
            le=MAX_SHARING_MINUTES,
            description="How long to share (5 minutes to 24 hours).",
        ),
    ] = DEFAULT_SHARING_MINUTES


class ConsentRead(BaseModel):
    """Whether the caller currently shares their location on this trip."""

    enabled: bool
    until: datetime | None = Field(description="When sharing lapses; null when off.")


class PositionUpdate(BaseModel):
    """The caller's current position, sent every few minutes while sharing."""

    model_config = ConfigDict(extra="forbid")

    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    accuracy_m: Annotated[
        float | None, Field(ge=0, le=100_000, description="Radius in metres.")
    ] = None


class LocationRead(BaseModel):
    """One member's last position, still valid."""

    profile_id: UUID = Field(description="Matches `profile_id` of the members list.")
    display_name: str
    latitude: float
    longitude: float
    accuracy_m: float | None
    recorded_at: datetime
    expires_at: datetime = Field(description="The position is not returned after this.")
    is_me: bool


class LocationSort(StrEnum):
    """Sort keys of the location list."""

    RECORDED_AT = "recorded_at"


class LocationFilters(ListFilters):
    """Filters of the location list."""

    mine: bool | None = Field(
        default=None, description="true: only my position, false: only others'."
    )


class LocationQuery(PageParams, LocationFilters):
    """Query of ``GET .../locations``."""

    sort: LocationSort = LocationSort.RECORDED_AT
    dir: SortDir = SortDir.DESC
