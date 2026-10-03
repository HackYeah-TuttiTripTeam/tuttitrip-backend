"""Pure DTOs of the list contract (see AGENTS.md, "Lists")."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_SIZE = 20
MAX_SIZE = 100
MAX_BULK_IDS = 100


class SortDir(StrEnum):
    """Sort direction."""

    ASC = "asc"
    DESC = "desc"


class PageParams(BaseModel):
    """Query parameters every list endpoint accepts.

    An endpoint subclasses this and adds `sort` (its own enum, mapped to
    columns in its `db.py`) and its filter fields.
    """

    model_config = ConfigDict(extra="forbid")

    page: Annotated[int, Field(ge=1, description="Page number, from 1.")] = 1
    size: Annotated[
        int, Field(ge=1, le=MAX_SIZE, description="Items per page (max 100).")
    ] = DEFAULT_SIZE
    dir: Annotated[SortDir, Field(description="Sort direction.")] = SortDir.ASC

    @property
    def offset(self) -> int:
        """Rows to skip.

        Returns:
            `(page - 1) * size`.
        """
        return (self.page - 1) * self.size


class Page[T](BaseModel):
    """One page of a list, with the total of matching rows."""

    items: list[T]
    total: Annotated[int, Field(ge=0, description="Rows matching the filters.")]
    page: Annotated[int, Field(ge=1)]
    size: Annotated[int, Field(ge=1, le=MAX_SIZE)]
    pages: Annotated[int, Field(ge=0, description="Pages in total; 0 when empty.")]

    @classmethod
    def of(cls, items: list[T], total: int, params: PageParams) -> Self:
        """Build a page from its rows and the total.

        Args:
            items: Rows of this page.
            total: Rows matching the filters, across all pages.
            params: The request's page parameters.

        Returns:
            The page; one past the end has empty `items` and the real `total`.
        """
        return cls(
            items=items,
            total=total,
            page=params.page,
            size=params.size,
            pages=-(-total // params.size),
        )


class BulkSelection[F: BaseModel](BaseModel):
    """What a bulk operation acts on: `ids` (max 100) or the list's filters.

    Exactly one is set, never both. The service always intersects the selection
    with what the caller may touch.
    """

    ids: Annotated[list[str] | None, Field(min_length=1, max_length=MAX_BULK_IDS)] = (
        None
    )
    filters: F | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.ids is None) == (self.filters is None):
            msg = "Provide either ids or filters, not both and not neither."
            raise ValueError(msg)
        return self
