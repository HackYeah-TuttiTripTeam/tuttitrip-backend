"""Paginated SQL: one page plus the count of matching rows."""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute

from tuttitrip.shared.pagination.schemas import Page, PageParams, SortDir

type Column = ColumnElement[Any] | QueryableAttribute[Any]
"""A sortable column: a core column or an ORM attribute."""


def ordering[S: StrEnum](
    columns: Mapping[S, Column],
    sort: S,
    id_column: Column,
) -> tuple[Column, ...]:
    """Resolve an endpoint's sort enum to columns, with `id` as the last key.

    Args:
        columns: The endpoint's own map from its sort enum to columns.
        sort: The validated enum value from the query.
        id_column: The unique id column, the tie-breaker.

    Returns:
        Columns to pass to `paginate` as `order`.
    """
    column = columns[sort]
    return (column,) if column is id_column else (column, id_column)


async def paginate[T](
    session: AsyncSession,
    stmt: Select[T],
    params: PageParams,
    order: Sequence[Column],
) -> Page[T]:
    """Run `stmt` as one page and count the rows it matches.

    The order keys come from `ordering` (never a client-provided column name),
    with `id` last so pages are stable. The count reuses the same `WHERE` and is
    skipped when the first page is not full.

    Args:
        session: Database session.
        stmt: Filtered, scoped select without ordering or limits.
        params: Page, size and direction from the request.
        order: Sort keys, the unique id last.

    Returns:
        The page. A page past the end has empty items and the real total.
    """
    keys = [k.desc() if params.dir is SortDir.DESC else k.asc() for k in order]
    page_stmt = stmt.order_by(*keys).limit(params.size).offset(params.offset)
    items = cast("list[T]", (await session.scalars(page_stmt)).all())
    if params.page == 1 and len(items) < params.size:
        return Page[T].of(items, len(items), params)
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = await session.scalar(count_stmt) or 0
    return Page[T].of(items, total, params)
