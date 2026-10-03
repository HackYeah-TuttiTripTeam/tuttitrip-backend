"""Trip roles and ``TripAccess``, the object-level guard of trip routes."""

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError, TripRoleError

BOB = AuthenticatedUser(sub="auth0|bob")
TRIP = uuid.uuid4()


def test_trip_roles_are_ordered() -> None:
    assert TripRole.HOST.satisfies(TripRole.CO_HOST)
    assert TripRole.CO_HOST.satisfies(TripRole.MEMBER)
    assert not TripRole.MEMBER.satisfies(TripRole.CO_HOST)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    authorize(app, BOB)  # feature permissions: everything
    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app) as test_client:
        yield test_client


def test_non_member_gets_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = TripNotFoundError(str(TRIP))
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=error))
    response = client.get(path("list_profiles", trip_id=TRIP))
    assert response.status_code == 404
    assert response.json() == {"detail": "Trip not found"}


def test_member_with_too_low_a_role_gets_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = TripRoleError("Trip role 'co_host' required (you are 'member')")
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=error))
    body = {"trip_id": str(TRIP), "request": "Gdańsk"}
    response = client.post(path("start_plan_job"), json=body)
    assert response.status_code == 403


def test_member_reads_the_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    membership = TripMembership(trip_id=TRIP, sub=BOB.sub, role=TripRole.MEMBER)
    check = AsyncMock(return_value=membership)
    monkeypatch.setattr(trip_service, "get_membership", check)
    monkeypatch.setattr(
        profile_service.db, "select_profiles_by_trip", AsyncMock(return_value=[])
    )
    response = client.get(path("list_profiles", trip_id=TRIP))
    assert response.status_code == 200
    assert response.json() == []
    _, trip_id, sub, min_role = check.call_args.args
    assert (trip_id, sub, min_role) == (TRIP, BOB.sub, TripRole.MEMBER)
