"""DTOs of the Takeout import."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class TakeoutSkipReason(StrEnum):
    """Why a saved place was not imported; the client writes the text."""

    MISSING_TITLE = "missing_title"
    DUPLICATE = "duplicate"
    NOT_IN_CATALOG = "not_in_catalog"
    AMBIGUOUS = "ambiguous"


class TakeoutMatched(BaseModel):
    """A saved place found in the catalog."""

    line: int = Field(ge=2, description="Line of the file (the header is line 1).")
    title: str
    place_id: UUID
    place_name: str
    liked: bool = Field(
        description="False when the person had already rated the place (kept)."
    )


class TakeoutUnmatched(BaseModel):
    """A saved place that was not imported, with the reason."""

    line: int = Field(ge=2, description="Line of the file (the header is line 1).")
    title: str
    reason: TakeoutSkipReason


class TakeoutImportRead(BaseModel):
    """Result of an import: what matched and what did not."""

    profile_id: UUID
    city_slug: str
    total: int = Field(ge=1, description="Saved places in the file.")
    liked: int = Field(ge=0, description="Places now rated `want` by the person.")
    kept: int = Field(
        ge=0, description="Matched places the person had already rated, unchanged."
    )
    matched: list[TakeoutMatched]
    unmatched: list[TakeoutUnmatched]
