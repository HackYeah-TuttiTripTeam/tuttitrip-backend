"""The list contract: Page[T], PageParams, filters, ordering, paginate, selected."""

import asyncio
from enum import StrEnum
from typing import Annotated, cast
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from sqlalchemy import ClauseElement, Integer, Select, String, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from tuttitrip.shared.db.pagination import ordering, paginate, selected
from tuttitrip.shared.pagination.schemas import (
    DEFAULT_SIZE,
    MAX_SIZE,
    BulkSelection,
    ListFilters,
    Page,
    PageParams,
    SortDir,
)


class Base(DeclarativeBase):
    pass


class Thing(Base):
    __tablename__ = "things"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # ty: ignore[unsound-assignment]
    owner: Mapped[str] = mapped_column(String)  # ty: ignore[unsound-assignment]
    name: Mapped[str] = mapped_column(String)  # ty: ignore[unsound-assignment]


class ThingSort(StrEnum):
    NAME = "name"
    ID = "id"
    LOWER_NAME = "lower_name"


COLUMNS = {
    ThingSort.NAME: Thing.name,
    ThingSort.ID: Thing.id,
    ThingSort.LOWER_NAME: func.lower(Thing.name),
}


class ThingFilters(ListFilters):
    prefix: str | None = None


class ThingQuery(PageParams, ThingFilters):
    sort: ThingSort = ThingSort.NAME


def apply_filters(stmt: Select[Thing], filters: ThingFilters) -> Select[Thing]:
    if filters.prefix is not None:
        stmt = stmt.where(Thing.name.startswith(filters.prefix))
    return stmt


def scoped(owner: str) -> Select[Thing]:
    return select(Thing).where(Thing.owner == owner)


ROWS = [Thing(id=i, owner="me", name=f"t{i}") for i in range(1, 13)]


class FakeSession:
    """Answers `scalars` with the requested slice and `scalar` with the count."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.statements: list[ClauseElement] = []

    async def scalars(self, stmt: Select[Thing]) -> object:
        self.statements.append(stmt)
        params = stmt.compile().params
        limit, offset = params["param_1"], params["param_2"]
        rows = ROWS[: self.total][offset : offset + limit]
        return type("R", (), {"all": lambda _self: rows})()

    async def scalar(self, stmt: ClauseElement) -> int:
        self.statements.append(stmt)
        return self.total


def sql(stmt: ClauseElement) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def run_page(total: int, params: PageParams) -> tuple[Page[Thing], FakeSession]:
    session = FakeSession(total)
    page = asyncio.run(
        paginate(
            cast("AsyncSession", session),
            scoped("me"),
            params,
            ordering(COLUMNS, ThingSort.NAME, Thing.id),
        )
    )
    return page, session


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
    assert Page[int].of([], total=100, params=PageParams(size=100)).pages == 1


def test_ordering_puts_id_last_and_never_twice() -> None:
    assert ordering(COLUMNS, ThingSort.NAME, Thing.id) == (Thing.name, Thing.id)
    assert ordering(COLUMNS, ThingSort.ID, Thing.id) == (Thing.id,)


def test_paginate_is_typed_without_casts() -> None:
    page, _ = run_page(12, PageParams(size=5))
    first: Thing = page.items[0]
    assert first.id == 1


def test_paginate_orders_with_id_last_nulls_last_and_limits() -> None:
    page, session = run_page(12, PageParams(page=2, size=5, dir=SortDir.DESC))
    assert [t.id for t in page.items] == [6, 7, 8, 9, 10]
    assert (page.total, page.pages, page.page, page.size) == (12, 3, 2, 5)
    page_sql, count_sql = (sql(s) for s in session.statements)
    assert "ORDER BY things.name DESC NULLS LAST, things.id DESC NULLS LAST" in page_sql
    assert "LIMIT" in page_sql
    assert "OFFSET" in page_sql
    assert "things.owner = " in page_sql
    assert "count(*)" in count_sql
    assert "things.owner = " in count_sql
    assert "ORDER BY" not in count_sql
    assert "LIMIT" not in count_sql


def test_expression_column_is_ordered() -> None:
    session = FakeSession(3)
    asyncio.run(
        paginate(
            cast("AsyncSession", session),
            scoped("me"),
            PageParams(),
            ordering(COLUMNS, ThingSort.LOWER_NAME, Thing.id),
        )
    )
    assert "ORDER BY lower(things.name) ASC NULLS LAST, things.id" in sql(
        session.statements[0]
    )


def test_non_empty_short_page_skips_the_count_on_any_page() -> None:
    page, session = run_page(3, PageParams())
    assert (page.total, len(session.statements)) == (3, 1)
    page, session = run_page(12, PageParams(page=3, size=5))
    assert (len(page.items), page.total, len(session.statements)) == (2, 12, 1)


def test_full_page_counts() -> None:
    page, session = run_page(12, PageParams(size=5))
    assert (page.total, len(session.statements)) == (12, 2)


def test_empty_first_page_is_zero_without_count() -> None:
    page, session = run_page(0, PageParams())
    assert (page.items, page.total, page.pages, len(session.statements)) == (
        [],
        0,
        0,
        1,
    )


def test_page_past_the_end_is_empty_with_the_real_total() -> None:
    page, session = run_page(12, PageParams(page=9, size=5))
    assert (page.items, page.total, page.pages, len(session.statements)) == (
        [],
        12,
        3,
        2,
    )


def test_bulk_selection_is_ids_xor_filters() -> None:
    assert BulkSelection[ThingFilters](ids=["a"]).filters is None
    assert BulkSelection[ThingFilters](filters=ThingFilters()).ids is None
    bad_bodies: list[dict[str, object]] = [
        {},
        {"ids": ["a"], "filters": {}},
        {"ids": []},
        {"ids": [""]},
        {"ids": ["x" * 65]},
        {"ids": [str(i) for i in range(101)]},
        {"filters": {"unknown": 1}},
    ]
    for bad in bad_bodies:
        with pytest.raises(ValidationError):
            BulkSelection[ThingFilters].model_validate(bad)


def test_bulk_ids_are_deduplicated_and_can_use_the_real_id_type() -> None:
    assert BulkSelection[ThingFilters](ids=["a", "b", "a"]).ids == ["a", "b"]
    uid = UUID(int=1)
    assert BulkSelection[ThingFilters, UUID](ids=[uid, uid]).ids == [uid]
    with pytest.raises(ValidationError):
        BulkSelection[ThingFilters, UUID].model_validate({"ids": ["nope"]})


def test_selected_ids_stay_inside_the_callers_scope() -> None:
    by_ids = selected(
        scoped("me"), Thing.id, BulkSelection[ThingFilters](ids=["1"]), apply_filters
    )
    text = sql(by_ids)
    assert text.startswith("SELECT things.id")
    assert "things.owner = " in text
    assert "things.id IN" in text
    by_filter = selected(
        scoped("me"),
        Thing.id,
        BulkSelection[ThingFilters](filters=ThingFilters(prefix="t")),
        apply_filters,
    )
    text = sql(by_filter)
    assert "things.owner = " in text
    assert "starts" in text.lower() or "LIKE" in text


class ThingRead(BaseModel):
    id: int


def make_app() -> tuple[FastAPI, FakeSession]:
    session = FakeSession(12)
    app = FastAPI()

    def get_session() -> FakeSession:
        return session

    @app.get("/things")
    async def things(
        q: Annotated[ThingQuery, Query()],
        db: Annotated[FakeSession, Depends(get_session)],
    ) -> Page[ThingRead]:
        page = await paginate(
            cast("AsyncSession", db),
            apply_filters(scoped("me"), q),
            q,
            ordering(COLUMNS, q.sort, Thing.id),
        )
        return Page[ThingRead].of(
            [ThingRead(id=t.id) for t in page.items], page.total, q
        )

    return app, session


def test_endpoint_paginates_and_rejects_bad_input_without_a_query() -> None:
    app, session = make_app()
    client = TestClient(app)
    body = client.get("/things?page=2&size=5&sort=name").json()
    assert [i["id"] for i in body["items"]] == [6, 7, 8, 9, 10]
    assert (body["total"], body["pages"]) == (12, 3)
    assert client.get("/things?page=9&size=5").json()["items"] == []
    served = len(session.statements)
    for query in (
        "page=0",
        "size=1000",
        "sort=password",
        "dir=sideways",
        "nope=1",
    ):
        assert client.get(f"/things?{query}").status_code == 422
    assert len(session.statements) == served


def test_openapi_documents_sort_values_and_size_bounds() -> None:
    app, _ = make_app()
    params = {
        p["name"]: p for p in app.openapi()["paths"]["/things"]["get"]["parameters"]
    }
    assert {"page", "size", "dir", "sort", "prefix"} <= set(params)
    assert params["size"]["schema"]["minimum"] == 1
    assert params["size"]["schema"]["maximum"] == MAX_SIZE
    assert params["page"]["schema"]["minimum"] == 1
    schemas = app.openapi()["components"]["schemas"]
    assert schemas["ThingSort"]["enum"] == ["name", "id", "lower_name"]
