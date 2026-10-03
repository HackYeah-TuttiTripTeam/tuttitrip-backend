"""The list contract: Page[T], PageParams, ordering and paginate()."""

import asyncio
from enum import StrEnum
from typing import Annotated, cast

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import ClauseElement, Integer, Select, String, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from tuttitrip.shared.db.pagination import ordering, paginate
from tuttitrip.shared.pagination.api import PageQuery
from tuttitrip.shared.pagination.schemas import (
    DEFAULT_SIZE,
    MAX_SIZE,
    BulkSelection,
    Page,
    PageParams,
    SortDir,
)


class Base(DeclarativeBase):
    pass


class Thing(Base):
    __tablename__ = "things"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # ty: ignore[unsound-assignment]
    name: Mapped[str] = mapped_column(String)  # ty: ignore[unsound-assignment]


class ThingSort(StrEnum):
    NAME = "name"
    ID = "id"


COLUMNS = {ThingSort.NAME: Thing.name, ThingSort.ID: Thing.id}
ROWS = [Thing(id=i, name=f"t{i}") for i in range(1, 13)]


class FakeSession:
    """Answers `scalars` with the requested slice and `scalar` with the count."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.statements: list[ClauseElement] = []

    async def scalars(self, stmt: Select[tuple[Thing]]) -> object:
        self.statements.append(stmt)
        compiled = stmt.compile()
        limit, offset = compiled.params["param_1"], compiled.params["param_2"]
        rows = ROWS[: self.total][offset : offset + limit]
        return type("R", (), {"all": lambda _self: rows})()

    async def scalar(self, stmt: ClauseElement) -> int:
        self.statements.append(stmt)
        return self.total


def sql(stmt: ClauseElement) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_defaults_and_bounds() -> None:
    params = PageParams()
    assert (params.page, params.size, params.dir) == (1, DEFAULT_SIZE, SortDir.ASC)
    assert PageParams(page=3, size=5).offset == 10
    for bad in ({"page": 0}, {"size": 0}, {"size": MAX_SIZE + 1}, {"dir": "up"}):
        with pytest.raises(ValidationError):
            PageParams.model_validate(bad)


def test_page_counts_pages() -> None:
    page = Page[int].of([1], total=11, params=PageParams(size=5))
    assert (page.pages, page.total) == (3, 11)
    assert Page[int].of([], total=0, params=PageParams()).pages == 0


def test_ordering_puts_id_last_and_never_twice() -> None:
    assert ordering(COLUMNS, ThingSort.NAME, Thing.id) == (Thing.name, Thing.id)
    assert ordering(COLUMNS, ThingSort.ID, Thing.id) == (Thing.id,)


def test_paginate_orders_with_id_last_and_limits() -> None:
    asyncio.run(_test_paginate_orders_with_id_last_and_limits())


async def _test_paginate_orders_with_id_last_and_limits() -> None:
    session = FakeSession(total=12)
    params = PageParams(page=2, size=5, dir=SortDir.DESC)
    page = await paginate(
        cast("AsyncSession", session),
        select(Thing).where(Thing.name != "x"),
        params,
        ordering(COLUMNS, ThingSort.NAME, Thing.id),
    )
    assert [t.id for t in page.items] == [6, 7, 8, 9, 10]
    assert (page.total, page.pages, page.page, page.size) == (12, 3, 2, 5)
    page_sql, count_sql = (sql(s) for s in session.statements)
    assert "ORDER BY things.name DESC, things.id DESC" in page_sql
    assert "LIMIT" in page_sql
    assert "OFFSET" in page_sql
    assert "count(*)" in count_sql
    assert "things.name != " in count_sql
    assert "ORDER BY" not in count_sql
    assert "LIMIT" not in count_sql


def test_short_first_page_skips_the_count() -> None:
    asyncio.run(_test_short_first_page_skips_the_count())


async def _test_short_first_page_skips_the_count() -> None:
    session = FakeSession(total=3)
    page = await paginate(
        cast("AsyncSession", session),
        select(Thing),
        PageParams(),
        (Thing.id,),
    )
    assert (page.total, len(page.items)) == (3, 3)
    assert len(session.statements) == 1


def test_page_past_the_end_is_empty_with_the_real_total() -> None:
    asyncio.run(_test_page_past_the_end_is_empty_with_the_real_total())


async def _test_page_past_the_end_is_empty_with_the_real_total() -> None:
    session = FakeSession(total=12)
    page = await paginate(
        cast("AsyncSession", session),
        select(Thing),
        PageParams(page=9, size=5),
        (Thing.id,),
    )
    assert (page.items, page.total, page.pages) == ([], 12, 3)


def test_bulk_selection_is_ids_xor_filters() -> None:
    class F(PageParams):
        pass

    assert BulkSelection[F](ids=["a"]).filters is None
    assert BulkSelection[F](filters=F()).ids is None
    for bad in ({}, {"ids": ["a"], "filters": {}}, {"ids": []}):
        with pytest.raises(ValidationError):
            BulkSelection[F].model_validate(bad)
    with pytest.raises(ValidationError):
        BulkSelection[F](ids=[str(i) for i in range(101)])


def test_query_model_gives_422_and_openapi_parameters() -> None:
    class SortQuery(PageParams):
        sort: ThingSort = ThingSort.NAME

    app = FastAPI()

    @app.get("/things")
    async def things(q: Annotated[SortQuery, Query()]) -> Page[int]:
        return Page[int].of([], 0, q)

    @app.get("/plain")
    async def plain(q: PageQuery) -> Page[int]:
        return Page[int].of([], 0, q)

    client = TestClient(app)
    assert client.get("/things?page=2&size=5&sort=name&dir=desc").status_code == 200
    for query in ("page=0", "size=1000", "sort=password", "dir=sideways"):
        assert client.get(f"/things?{query}").status_code == 422
    assert client.get("/plain?size=101").status_code == 422
    names = {p["name"] for p in app.openapi()["paths"]["/plain"]["get"]["parameters"]}
    assert names == {"page", "size", "dir"}
