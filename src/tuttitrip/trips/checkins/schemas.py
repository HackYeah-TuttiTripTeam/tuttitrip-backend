"""Check-in DTOs."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tuttitrip.shared.pagination.schemas import ListFilters, PageParams, SortDir

MAX_ACCOMMODATION = 200
MAX_ROOM = 20


class CheckinUpdate(BaseModel):
    """What a person tells the group: where they stay and the room number."""

    model_config = ConfigDict(extra="forbid")

    accommodation: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_ACCOMMODATION
        ),
        Field(description="Hotel, apartment or other place they stay at."),
    ]
    room: Annotated[
        str | None,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ROOM),
        Field(description="Room number or name; omit when there is none."),
    ] = None


class CheckinRead(BaseModel):
    """One check-in as the trip's members see it."""

    profile_id: UUID
    display_name: str
    accommodation: str
    room: str | None
    updated_at: datetime
    is_me: bool = Field(description="Whether this is the caller's own entry.")


class CheckinSort(StrEnum):
    """Sort keys of the check-in list."""

    UPDATED_AT = "updated_at"
    ACCOMMODATION = "accommodation"
    ROOM = "room"


class CheckinFilters(ListFilters):
    """Filters of the check-in list."""

    accommodation: Annotated[
        str | None,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_ACCOMMODATION
        ),
        Field(description="Case-insensitive part of the accommodation name."),
    ] = None


class CheckinQuery(PageParams, CheckinFilters):
    """Query of ``GET .../checkins``."""

    sort: CheckinSort = CheckinSort.ACCOMMODATION
    dir: SortDir = SortDir.ASC
