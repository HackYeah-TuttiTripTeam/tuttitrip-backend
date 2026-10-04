"""DTOs of the algorithm parameters."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from tuttitrip.planning.logic.params import AlgorithmParams
from tuttitrip.shared.pagination.schemas import ListFilters, PageParams, SortDir

NOTE_MAX_LENGTH = 500


class ParametersCreate(BaseModel):
    """A new version: the whole set (omitted fields take the default), plus a note.

    The ranges are those of ``AlgorithmParams`` (section 6 of docs/algorytm.md).
    """

    model_config = ConfigDict(extra="forbid")

    values: AlgorithmParams = Field(default_factory=AlgorithmParams)
    note: Annotated[str, Field(min_length=1, max_length=NOTE_MAX_LENGTH)] | None = None


class ParametersRead(BaseModel):
    """A stored version of the parameters."""

    version: int = Field(ge=0, description="0 is the built-in default (no row).")
    values: AlgorithmParams
    note: str | None = None
    created_by_sub: str | None = Field(
        default=None, description="The administrator; null for version 0."
    )
    created_at: datetime | None = None


class ParametersFilters(ListFilters):
    """No filters: the history is short and ordered by version."""


class ParametersQuery(PageParams, ParametersFilters):
    """Query of ``GET /admin/planning/parameters/versions``."""

    dir: SortDir = SortDir.DESC
