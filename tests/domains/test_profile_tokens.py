"""Voting link tokens issued by a co-host for a profile of the trip."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.profiles import db as profile_db
from tuttitrip.profiles.models import Profile
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions import db as permission_db
from tuttitrip.shared.permissions.models import AccessToken
from tuttitrip.shared.permissions.schemas import TokenScope
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripRoleError

HOST = AuthenticatedUser(sub="auth0|host")
TRIP = uuid.uuid4()
PROFILE = uuid.uuid4()
NOW = datetime(2026, 10, 3, tzinfo=UTC)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    authorize(app, HOST)
    session = AsyncMock()
    session.add = lambda _row: None
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client


def _role(monkeypatch: pytest.MonkeyPatch, role: TripRole) -> None:
    def check(_s: object, _t: object, _sub: str, min_role: TripRole) -> TripMembership:
        if not role.satisfies(min_role):
            msg = f"Trip role '{min_role}' required (you are '{role}')"
            raise TripRoleError(msg)
        return TripMembership(trip_id=TRIP, sub=HOST.sub, role=role)

    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=check))


def _profile(monkeypatch: pytest.MonkeyPatch, profile: Profile | None) -> None:
    monkeypatch.setattr(profile_db, "select_profile", AsyncMock(return_value=profile))


def _stored(row: AccessToken) -> AccessToken:
    row.id = uuid.uuid4()
    row.created_at = NOW
    return row


def test_a_co_host_creates_a_token_and_sees_it_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    _profile(monkeypatch, Profile(id=PROFILE, trip_id=TRIP))
    inserted: list[AccessToken] = []
    monkeypatch.setattr(
        permission_db, "count_active_access_tokens", AsyncMock(return_value=0)
    )

    def insert(_session: object, row: AccessToken) -> AccessToken:
        inserted.append(_stored(row))
        return row

    monkeypatch.setattr(
        permission_db, "insert_access_token", AsyncMock(side_effect=insert)
    )
    response = client.post(
        path("create_vote_token", trip_id=TRIP, profile_id=PROFILE),
        json={"expires_in_days": 7},
    )
    assert response.status_code == 201
    body = response.json()
    (row,) = inserted
    assert (row.scope, row.trip_id, row.profile_id) == (TokenScope.VOTE, TRIP, PROFILE)
    assert row.created_by == HOST.sub
    assert body["token"] not in {row.token_hash, str(row.id)}
    assert len(row.token_hash) == 64
    assert response.headers["cache-control"] == "no-store"


def test_a_plain_member_cannot_create_a_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.MEMBER)
    response = client.post(
        path("create_vote_token", trip_id=TRIP, profile_id=PROFILE), json={}
    )
    assert response.status_code == 403


def test_a_profile_of_another_trip_is_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.HOST)
    _profile(monkeypatch, None)
    response = client.post(
        path("create_vote_token", trip_id=TRIP, profile_id=PROFILE), json={}
    )
    assert response.status_code == 404


def test_revoking_sets_revoked_at_and_is_idempotent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    _profile(monkeypatch, Profile(id=PROFILE, trip_id=TRIP))
    row = _stored(
        AccessToken(
            token_hash="a" * 64,
            scope=TokenScope.VOTE,
            trip_id=TRIP,
            profile_id=PROFILE,
            expires_at=NOW,
            revoked_at=None,
            created_by=HOST.sub,
        )
    )
    monkeypatch.setattr(
        permission_db, "select_access_token", AsyncMock(return_value=row)
    )
    url = path("revoke_access_token", trip_id=TRIP, profile_id=PROFILE, token_id=row.id)
    first = client.delete(url)
    assert first.status_code == 200
    assert first.json()["revoked_at"] is not None
    revoked_at = row.revoked_at
    assert client.delete(url).status_code == 200
    assert row.revoked_at == revoked_at


def test_revoking_an_unknown_token_is_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    _profile(monkeypatch, Profile(id=PROFILE, trip_id=TRIP))
    monkeypatch.setattr(
        permission_db, "select_access_token", AsyncMock(return_value=None)
    )
    url = path(
        "revoke_access_token", trip_id=TRIP, profile_id=PROFILE, token_id=uuid.uuid4()
    )
    assert client.delete(url).status_code == 404


def test_a_profile_with_an_account_gets_no_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    _profile(monkeypatch, Profile(id=PROFILE, trip_id=TRIP, user_sub="auth0|ann"))
    response = client.post(
        path("create_vote_token", trip_id=TRIP, profile_id=PROFILE), json={}
    )
    assert response.status_code == 409


def test_the_sixth_active_token_is_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    _profile(monkeypatch, Profile(id=PROFILE, trip_id=TRIP))
    monkeypatch.setattr(
        permission_db, "count_active_access_tokens", AsyncMock(return_value=5)
    )
    response = client.post(
        path("create_vote_token", trip_id=TRIP, profile_id=PROFILE), json={}
    )
    assert response.status_code == 409


def test_a_co_host_lists_tokens_without_the_secret(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    _profile(monkeypatch, Profile(id=PROFILE, trip_id=TRIP))
    row = _stored(
        AccessToken(
            token_hash="a" * 64,
            scope=TokenScope.VOTE,
            trip_id=TRIP,
            profile_id=PROFILE,
            expires_at=NOW,
            revoked_at=None,
            last_used_at=None,
            created_by=HOST.sub,
        )
    )
    monkeypatch.setattr(
        permission_db, "select_access_tokens", AsyncMock(return_value=[row])
    )
    response = client.get(path("list_access_tokens", trip_id=TRIP, profile_id=PROFILE))
    assert response.status_code == 200
    (item,) = response.json()
    assert set(item) == {
        "id",
        "scope",
        "trip_id",
        "profile_id",
        "expires_at",
        "revoked_at",
        "created_at",
        "last_used_at",
    }
    assert "a" * 64 not in response.text


def test_a_plain_member_cannot_list_tokens(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.MEMBER)
    response = client.get(path("list_access_tokens", trip_id=TRIP, profile_id=PROFILE))
    assert response.status_code == 403
