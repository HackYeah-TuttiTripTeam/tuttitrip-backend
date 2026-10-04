"""Notification list and unread counter: validation, permissions, filters in SQL."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from tests.shared.paths import path
from tests.shared.tokens import bearer, make_verifier
from tuttitrip.main import create_app
from tuttitrip.notifications import db
from tuttitrip.notifications.models import Notification
from tuttitrip.notifications.schemas import (
    NotificationActionCode,
    NotificationFilter,
    NotificationQuery,
    NotificationSort,
    UnreadCount,
)
from tuttitrip.notifications.services import notification_service
from tuttitrip.shared.auth.api import get_token_verifier
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.pagination.schemas import Page, PageParams, SortDir
from tuttitrip.shared.permissions.api import get_user_grants
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature

SUB = "google-oauth2|42"


@pytest.fixture
def app() -> FastAPI:
    application = create_app()
    verifier = make_verifier()
    application.dependency_overrides[get_token_verifier] = lambda: verifier
    application.dependency_overrides[get_session] = lambda: None
    application.dependency_overrides[get_user_grants] = lambda: [
        Grant(Feature.NOTIFICATIONS, Access.READ)
    ]
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def call_args(mock: AsyncMock) -> tuple[Any, ...]:
    call = mock.await_args
    assert call is not None
    return call.args


def empty_page() -> Page[object]:
    return Page[object].of([], 0, PageParams())


def test_list_passes_the_caller_and_defaults(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = AsyncMock(return_value=empty_page())
    monkeypatch.setattr(notification_service, "list_notifications", listing)
    response = client.get(path("list_notifications"), headers=bearer())
    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "size": 20,
        "pages": 0,
    }
    _session, caller, query = call_args(listing)
    assert caller == SUB
    assert query.sort is NotificationSort.CREATED_AT
    assert query.dir is SortDir.DESC
    assert query.read is None


def test_list_parses_repeated_types_and_all_filters(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = AsyncMock(return_value=empty_page())
    monkeypatch.setattr(notification_service, "list_notifications", listing)
    params: list[tuple[str, str | float | None]] = [
        ("type", "veto_added"),
        ("type", "member_joined"),
        ("read", "false"),
        ("sort", "type"),
        ("dir", "asc"),
        ("page", "2"),
        ("size", "5"),
        ("created_from", "2026-10-01T00:00:00Z"),
        ("created_to", "2026-10-02"),
    ]
    response = client.get(path("list_notifications"), params=params, headers=bearer())
    assert response.status_code == 200
    query = call_args(listing)[2]
    assert query.type == ["veto_added", "member_joined"]
    assert query.read is False
    assert (query.sort, query.dir, query.page, query.size) == (
        NotificationSort.TYPE,
        SortDir.ASC,
        2,
        5,
    )
    assert query.created_from == datetime(2026, 10, 1, tzinfo=UTC)
    assert query.created_to == datetime(2026, 10, 2, tzinfo=UTC)


@pytest.mark.parametrize(
    "params",
    [
        {"size": 101},
        {"size": 0},
        {"page": 0},
        {"sort": "read_at"},
        {"dir": "up"},
        {"read": "maybe"},
        {"trip_id": "not-a-uuid"},
        {"created_from": "yesterday"},
        {"unknown": "1"},
        {"type": "x" * 65},
    ],
    ids=lambda p: "-".join(f"{k}={v}" for k, v in p.items()),
)
def test_list_rejects_bad_parameters(
    client: TestClient, params: dict[str, str | int]
) -> None:
    response = client.get(path("list_notifications"), params=params, headers=bearer())
    assert response.status_code == 422


def test_unread_count_returns_the_count(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    count = AsyncMock(return_value=UnreadCount(count=3))
    monkeypatch.setattr(notification_service, "unread_count", count)
    response = client.get(path("unread_count"), headers=bearer())
    assert response.status_code == 200
    assert response.json() == {"count": 3}
    assert call_args(count)[1] == SUB


@pytest.mark.parametrize("name", ["list_notifications", "unread_count"])
def test_endpoints_need_a_token(client: TestClient, name: str) -> None:
    assert client.get(path(name)).status_code == 401


@pytest.mark.parametrize("name", ["list_notifications", "unread_count"])
def test_endpoints_need_the_permission(app: FastAPI, name: str) -> None:
    app.dependency_overrides[get_user_grants] = lambda: [
        Grant(Feature.TRIPS_CORE, Access.READ)
    ]
    response = TestClient(app).get(path(name), headers=bearer())
    assert response.status_code == 403
    assert "notifications:READ" in response.json()["detail"]


def sql(query: NotificationQuery) -> str:
    stmt = db.apply_filters(db.scoped(SUB), query)
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_every_query_is_scoped_to_the_caller() -> None:
    assert f"notifications.user_sub = '{SUB}'" in sql(NotificationQuery())


def test_filters_combine_with_and() -> None:
    text = sql(
        NotificationQuery(
            read=False,
            type=["veto_added"],
            created_from=datetime(2026, 10, 1, tzinfo=UTC),
            created_to=datetime(2026, 10, 2, tzinfo=UTC),
        )
    )
    assert "notifications.read_at IS NULL" in text
    assert "notifications.type IN ('veto_added')" in text
    assert "notifications.created_at >= '2026-10-01 00:00:00+00:00'" in text
    assert "notifications.created_at < '2026-10-02 00:00:00+00:00'" in text


def test_filter_model_is_shared_by_query_and_bulk() -> None:
    assert set(NotificationFilter.model_fields) <= set(NotificationQuery.model_fields)
    assert {"page", "size", "sort", "dir"}.isdisjoint(NotificationFilter.model_fields)


def test_a_row_from_a_newer_producer_does_not_break_the_list(
    caplog: pytest.LogCaptureFixture,
) -> None:
    row = Notification(
        id=uuid.uuid4(),
        user_sub=SUB,
        type="from_the_future",
        trip_id=None,
        params={"name": "Ola", "count": 3},
        actions=[{"code": "open_plan", "params": {}}, {"code": "teleport"}],
        read_at=None,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    read = notification_service.to_read(row)
    assert [a.code for a in read.actions] == [NotificationActionCode.OPEN_PLAN]
    assert read.params == {"name": "Ola", "count": "3"}
    assert "teleport" in caplog.text


def test_one_notification_is_returned_to_its_owner(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = Notification(
        id=uuid.uuid4(),
        user_sub=SUB,
        type="plan_ready",
        trip_id=None,
        params={},
        actions=[{"code": "teleport"}],
        read_at=None,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    select = AsyncMock(return_value=row)
    monkeypatch.setattr(db, "select_one", select)
    response = client.get(
        path("get_notification", notification_id=row.id), headers=bearer()
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(row.id)
    assert response.json()["actions"] == []  # tolerant like the list
    assert call_args(select)[1] == SUB


def test_an_unknown_or_foreign_notification_is_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "select_one", AsyncMock(return_value=None))
    response = client.get(
        path("get_notification", notification_id=uuid.uuid4()), headers=bearer()
    )
    assert response.status_code == 404


def test_a_bad_id_is_422_and_the_fixed_routes_still_win(client: TestClient) -> None:
    assert client.get("/api/v1/notifications/nope", headers=bearer()).status_code == 422
    assert path("unread_count").endswith("/unread-count")
    assert path("stream_notifications").endswith("/stream")


def test_getting_one_needs_a_token_and_the_permission(app: FastAPI) -> None:
    target = path("get_notification", notification_id=uuid.uuid4())
    assert TestClient(app).get(target).status_code == 401
    app.dependency_overrides[get_user_grants] = lambda: [
        Grant(Feature.TRIPS_CORE, Access.READ)
    ]
    assert TestClient(app).get(target, headers=bearer()).status_code == 403
