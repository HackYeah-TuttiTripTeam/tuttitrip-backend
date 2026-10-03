"""Paginated SQL: one page plus the count of matching rows, and bulk selections."""

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute

from tuttitrip.shared.pagination.schemas import (
    BulkSelection,
    ListFilters,
    Page,
    PageParams,
    SortDir,
)

type Column = ColumnElement[Any] | QueryableAttribute[Any]
"""A sortable column: a core column, an expression or an ORM attribute."""


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

    `stmt` selects a single entity, already scoped to the caller and filtered,
    without ordering or limits. A to-many filter must be an `EXISTS` (or
    `distinct`), never a join, or rows repeat and `total` is wrong. The order
    keys come from `ordering` (never a client-provided column name), `id` last
    so pages are stable. NULLs always sort last, in both directions.

    The count reuses the same `WHERE` and is skipped when the page is non-empty
    and short (the total is then `offset + len(items)`) and when page 1 is empty.
    An empty page past the end still counts, so it returns the real total.

    Args:
        session: Database session.
        stmt: Filtered, scoped select of one entity.
        params: Page, size and direction from the request.
        order: Sort keys, the unique id last.

    Returns:
        The page. A page past the end has empty items and the real total.
    """
    keys = [
        (k.desc() if params.dir is SortDir.DESC else k.asc()).nulls_last()
        for k in order
    ]
    page_stmt = stmt.order_by(*keys).limit(params.size).offset(params.offset)
    items = cast("list[T]", (await session.scalars(page_stmt)).all())
    if items and len(items) < params.size:
        return Page[T].of(items, params.offset + len(items), params)
    if not items and params.page == 1:
        return Page[T].of(items, 0, params)
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = await session.scalar(count_stmt) or 0
    return Page[T].of(items, total, params)


def selected[T, F: ListFilters, I](
    scoped: Select[T],
    id_column: Column,
    selection: BulkSelection[F, I],
    apply_filters: Callable[[Select[T], F], Select[T]],
) -> Select[Any]:
    """Select the ids a bulk operation acts on, always inside the caller's scope.

    Start from the same caller-scoped select `paginate` uses and pass the same
    `apply_filters` the list query uses, so ids outside the scope are ignored
    and a filter selection means exactly the rows the list shows.

    Args:
        scoped: The caller-scoped select (what the list starts from).
        id_column: The unique id column.
        selection: The request body: ids or filters.
        apply_filters: The list's own function adding filters to a select.

    Returns:
        A select of ids; use it as `Model.id.in_(...)` in the update or delete.
    """
    if selection.ids is not None:
        chosen = scoped.where(id_column.in_(selection.ids))
    elif selection.filters is not None:
        chosen = apply_filters(scoped, selection.filters)
    else:  # unreachable: BulkSelection validates exactly one
        msg = "Empty bulk selection"
        raise ValueError(msg)
    return chosen.with_only_columns(id_column)
