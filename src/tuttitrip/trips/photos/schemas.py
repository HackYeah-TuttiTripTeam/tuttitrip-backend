"""Photo DTOs."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from tuttitrip.shared.pagination.schemas import ListFilters, PageParams, SortDir


class PhotoRead(BaseModel):
    """A photo in the list, with its thumbnail inlined (no second request)."""

    id: UUID
    author_name: str | None = Field(
        description="Name from the author's profile; null once they left the trip."
    )
    is_mine: bool = Field(description="Whether the caller uploaded it.")
    content_type: str
    size_bytes: int
    created_at: datetime
    thumbnail: str = Field(
        description="Thumbnail as a `data:` URL, ready for an `<img src>`."
    )


class PhotoSort(StrEnum):
    """Sort keys of the photo list."""

    CREATED_AT = "created_at"
    SIZE_BYTES = "size_bytes"


class PhotoFilters(ListFilters):
    """Filters of the photo list."""

    mine: bool | None = Field(
        default=None, description="true: only my photos, false: only others'."
    )


class PhotoQuery(PageParams, PhotoFilters):
    """Query of ``GET .../photos``."""

    sort: PhotoSort = PhotoSort.CREATED_AT
    dir: SortDir = SortDir.DESC
