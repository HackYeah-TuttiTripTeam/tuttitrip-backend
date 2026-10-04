"""Marking notifications read or unread: body rules, permissions, the UPDATE."""

import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.shared.paths import path
from tests.shared.tokens import bearer, make_verifier
from tuttitrip.main import create_app
from tuttitrip.notifications.schemas import MarkResult, NotificationMark
from tuttitrip.notifications.services import notification_service
from tuttitrip.shared.auth.api import get_token_verifier
from tuttitrip.shared.db.api import get_session
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
        Grant(Feature.NOTIFICATIONS, Access.WRITE)
    ]
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def post(client: TestClient, body: dict[str, Any]) -> Any:  # ruff: ignore[any-type]
    return client.post(path("mark_notifications"), json=body, headers=bearer())


def test_marking_by_ids_calls_the_service_with_the_caller(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mark = AsyncMock(return_value=MarkResult(updated=2))
    monkeypatch.setattr(notification_service, "mark", mark)
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    response = post(client, {"read": True, "ids": ids})
    assert response.status_code == 200
    assert response.json() == {"updated": 2}
    call = mark.await_args
    assert call is not None
    _session, caller, selection = call.args
    assert caller == SUB
    assert selection.read is True
    assert [str(i) for i in selection.ids] == ids
    assert selection.filters is None


def test_an_empty_filter_means_everything(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mark = AsyncMock(return_value=MarkResult(updated=0))
    monkeypatch.setattr(notification_service, "mark", mark)
    assert post(client, {"read": False, "filters": {}}).status_code == 200
    selection = mark.await_args.args[2]  # ty: ignore[unresolved-attribute]
    assert selection.ids is None
    assert selection.filters is not None


@pytest.mark.parametrize(
    "body",
    [
        {"read": True},
        {"read": True, "ids": None, "filters": None},
        {"read": True, "ids": [str(uuid.uuid4())], "filters": {}},
        {"read": True, "ids": []},
        {"read": True, "ids": [str(uuid.uuid4()) for _ in range(101)]},
        {"read": True, "ids": ["not-a-uuid"]},
        {"ids": [str(uuid.uuid4())]},
        {"read": True, "filters": {"page": 2}},
        {"read": True, "filters": {"read": "maybe"}},
        {"read": True, "filter": {}},
    ],
    ids=[
        "neither",
        "both-null",
        "both",
        "empty-ids",
        "101-ids",
        "bad-uuid",
        "no-read",
        "paging-in-filter",
        "bad-filter",
        "wrong-key",
    ],
)
def test_invalid_bodies_are_422(client: TestClient, body: dict[str, Any]) -> None:
    assert post(client, body).status_code == 422


def test_ids_are_deduplicated_and_100_are_allowed() -> None:
    one = uuid.uuid4()
    selection = NotificationMark.model_validate({"read": True, "ids": [one, one]})
    assert selection.ids == [one]
    many = [str(uuid.uuid4()) for _ in range(100)]
    assert NotificationMark.model_validate({"read": True, "ids": many}).ids


def test_marking_needs_a_token(client: TestClient) -> None:
    response = client.post(path("mark_notifications"), json={"read": True, "ids": []})
    assert response.status_code == 401


def test_marking_needs_write_not_just_read(app: FastAPI) -> None:
    app.dependency_overrides[get_user_grants] = lambda: [
        Grant(Feature.NOTIFICATIONS, Access.READ)
    ]
    response = TestClient(app).post(
        path("mark_notifications"),
        json={"read": True, "filters": {}},
        headers=bearer(),
    )
    assert response.status_code == 403
    assert "notifications:WRITE" in response.json()["detail"]
