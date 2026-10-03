"""FastAPI binding of the list contract."""

from typing import Annotated

from fastapi import Query

from tuttitrip.shared.pagination.schemas import PageParams

PageQuery = Annotated[PageParams, Query()]
"""Query model: `page`, `size`, `dir`. Invalid values answer 422 (in OpenAPI)."""
