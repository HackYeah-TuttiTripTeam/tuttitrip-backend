"""Access without an account: ``token_access`` marker, X-Access-Token, service."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions import db
from tuttitrip.shared.permissions.models import AccessToken
from tuttitrip.shared.permissions.schemas import TokenScope
from tuttitrip.shared.permissions.services import token_service
from tuttitrip.shared.permissions.services.token_service import (
    InvalidTokenError,
    NewToken,
)

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
TRIP = uuid.uuid4()
PROFILE = uuid.uuid4()
TOKEN = "t" * 43
HEADERS = {"X-Access-Token": TOKEN}


def _row(**overrides: object) -> AccessToken:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "token_hash": token_service.hash_token(TOKEN),
        "scope": TokenScope.VOTE,
        "trip_id": TRIP,
        "profile_id": PROFILE,
        "expires_at": NOW + timedelta(days=1),
        "revoked_at": None,
        "created_by": "auth0|host",
        "created_at": NOW,
        "last_used_at": None,
    }
    return AccessToken(**(values | overrides))


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client(session: AsyncMock) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client


def _stored(monkeypatch: pytest.MonkeyPatch, row: AccessToken | None) -> AsyncMock:
    lookup = AsyncMock(return_value=row)
    monkeypatch.setattr(db, "select_access_token_by_hash", lookup)
    return lookup


def test_a_valid_token_gives_its_own_trip_and_profile(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = _stored(monkeypatch, _row(expires_at=datetime.now(UTC) + timedelta(1)))
    response = client.get(path("read_vote_access"), headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert (body["trip_id"], body["profile_id"], body["scope"]) == (
        str(TRIP),
        str(PROFILE),
        "vote",
    )
    # looked up by the hash, never by the plain token
    assert lookup.call_args.args[1] == token_service.hash_token(TOKEN)


def test_no_header_is_401_and_needs_no_account(client: TestClient) -> None:
    response = client.get(path("read_vote_access"))
    assert response.status_code == 401


@pytest.mark.parametrize(
    "row",
    [
        None,
        _row(expires_at=datetime(2020, 1, 1, tzinfo=UTC)),
        _row(revoked_at=datetime(2020, 1, 1, tzinfo=UTC)),
    ],
    ids=["unknown", "expired", "revoked"],
)
def test_unknown_expired_and_revoked_tokens_are_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, row: AccessToken | None
) -> None:
    _stored(monkeypatch, row)
    response = client.get(path("read_vote_access"), headers=HEADERS)
    assert response.status_code == 404
    assert TOKEN not in response.text


def test_the_token_route_is_documented_in_openapi(client: TestClient) -> None:
    operation = client.get("/api/v1/openapi.json").json()["paths"][
        "/api/v1/vote/access"
    ]["get"]
    assert operation["x-token-access"] == "vote"
    assert "404" in operation["responses"]
    assert "x-required-permission" not in operation


def test_authenticate_marks_the_last_use_at_most_once_a_minute(
    session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _row()
    _stored(monkeypatch, row)
    asyncio.run(token_service.authenticate(session, TOKEN, NOW))
    assert row.last_used_at == NOW
    asyncio.run(token_service.authenticate(session, TOKEN, NOW + timedelta(seconds=30)))
    assert row.last_used_at == NOW
    assert session.commit.await_count == 1


def test_authenticate_rejects_the_boundary_and_a_wrong_hash(
    session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stored(monkeypatch, _row(expires_at=NOW))
    with pytest.raises(InvalidTokenError):
        asyncio.run(token_service.authenticate(session, TOKEN, NOW))
    _stored(monkeypatch, _row(token_hash="0" * 64))
    with pytest.raises(InvalidTokenError):
        asyncio.run(token_service.authenticate(session, TOKEN, NOW))


def test_only_the_hash_is_stored_and_the_token_is_shown_once() -> None:
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock(
        side_effect=lambda row: (
            setattr(row, "id", uuid.uuid4()) or setattr(row, "created_at", NOW)
        )
    )
    new = NewToken(TokenScope.VOTE, TRIP, PROFILE, "auth0|host", timedelta(days=3))
    created = asyncio.run(token_service.create_token(session, new))
    (stored,) = [call.args[0] for call in session.add.call_args_list]
    assert stored.token_hash == token_service.hash_token(created.token)
    assert created.token not in {str(v) for v in vars(stored).values()}
    assert len(created.token) >= 43  # 32 random bytes, urlsafe base64
    assert "token" not in created.model_dump(exclude={"token"})
