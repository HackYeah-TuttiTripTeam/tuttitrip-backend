"""Pasted documents: stored by the host, limited to 20 000 characters."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.planning.linter import db
from tuttitrip.planning.linter.models import PastedDocument
from tuttitrip.planning.linter.schemas import MAX_DOCUMENT_CHARS
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError, TripRoleError

BOB = AuthenticatedUser(sub="auth0|bob")
TRIP = uuid.uuid4()
WRITE = (Grant(Feature.PLANNING_LINTER, Access.WRITE),)


def _client(grants: tuple[Grant, ...] = WRITE) -> TestClient:
    app = create_app()
    authorize(app, BOB, grants)
    session = AsyncMock()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    membership = TripMembership(trip_id=TRIP, sub=BOB.sub, role=TripRole.CO_HOST)
    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(return_value=membership)
    )

    async def insert(  # ruff: ignore[unused-async]
        _session: object, *, trip_id: uuid.UUID, kind: str, text: str, created_by: str
    ) -> PastedDocument:
        return PastedDocument(
            id=uuid.uuid4(),
            trip_id=trip_id,
            kind=kind,
            text=text,
            created_by=created_by,
            created_at=datetime.now(UTC),
        )

    monkeypatch.setattr(db, "insert_document", insert)
    with _client() as test_client:
        yield test_client


def _url() -> str:
    return path("create_document", trip_id=TRIP)


def test_host_stores_a_pasted_plan(client: TestClient) -> None:
    response = client.post(_url(), json={"kind": "plan", "text": "Dzień 1: Wawel"})
    assert response.status_code == 201
    body = response.json()
    assert body["trip_id"] == str(TRIP)
    assert body["created_by"] == BOB.sub
    assert body["text"] == "Dzień 1: Wawel"


def test_text_at_the_limit_is_accepted(client: TestClient) -> None:
    text = "a" * MAX_DOCUMENT_CHARS
    assert client.post(_url(), json={"kind": "offer", "text": text}).status_code == 201


def test_text_over_the_limit_is_422_with_a_readable_message(
    client: TestClient,
) -> None:
    text = "a" * (MAX_DOCUMENT_CHARS + 1)
    response = client.post(_url(), json={"kind": "offer", "text": text})
    assert response.status_code == 422
    assert "at most 20000 characters" in response.json()["detail"][0]["msg"]


@pytest.mark.parametrize(
    "body", [{"kind": "plan", "text": "  "}, {"kind": "x", "text": "a"}]
)
def test_blank_text_and_unknown_kind_are_422(
    client: TestClient, body: dict[str, str]
) -> None:
    assert client.post(_url(), json=body).status_code == 422


def test_read_only_grant_is_403() -> None:
    read_only = (Grant(Feature.PLANNING_LINTER, Access.READ),)
    with _client(read_only) as client:
        response = client.post(_url(), json={"kind": "plan", "text": "x"})
    assert response.status_code == 403


def test_other_peoples_trip_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(side_effect=TripNotFoundError("t"))
    )
    with _client() as client:
        response = client.post(_url(), json={"kind": "plan", "text": "x"})
    assert response.status_code == 404


def test_plain_member_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(side_effect=TripRoleError("co_host"))
    )
    with _client() as client:
        response = client.post(_url(), json={"kind": "plan", "text": "x"})
    assert response.status_code == 403


def test_without_token_is_401() -> None:
    with TestClient(create_app()) as anonymous:
        assert (
            anonymous.post(_url(), json={"kind": "plan", "text": "x"}).status_code
            == 401
        )


def test_documents_are_deleted_with_the_trip() -> None:
    (foreign_key,) = PastedDocument.__table__.c.trip_id.foreign_keys
    assert foreign_key.ondelete == "CASCADE"
