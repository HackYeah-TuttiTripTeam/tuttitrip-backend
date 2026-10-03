"""Lodging requirements: key dictionary, validation, guards and the PUT rules."""

import asyncio
import uuid
from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.accommodation import db
from tuttitrip.accommodation.logic.keys import KEYS, RequirementKind, is_known_key
from tuttitrip.accommodation.schemas import (
    RequirementItem,
    RequirementsRead,
    RequirementsWrite,
)
from tuttitrip.accommodation.services import requirements_service
from tuttitrip.accommodation.services.requirements_service import (
    RequirementsInvalidError,
)
from tuttitrip.main import create_app
from tuttitrip.places.schemas import Amenity
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import TripMembership, TripRead, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError

BOB = AuthenticatedUser(sub="auth0|bob")
TRIP = uuid.uuid4()
POOL: dict[str, Any] = {"kind": "amenity", "key": "pool", "hard": True}


def _trip(start: date | None, end: date | None) -> TripRead:
    return TripRead.model_validate(
        {
            "id": TRIP,
            "name": "Test",
            "destination": None,
            "created_at": "2026-10-03T10:00:00Z",
            "start_date": start,
            "end_date": end,
            "day_start": "09:00",
            "day_end": "19:00",
            "city_slug": None,
            "currency": None,
            "budget_total_min": None,
            "budget_total_max": None,
            "budget_day_min": None,
            "budget_day_max": None,
            "budget_flex_pct": 10,
            "fairness_alpha": 1.0,
            "my_role": TripRole.CO_HOST,
        }
    )


def _membership(role: TripRole) -> TripMembership:
    return TripMembership(trip_id=TRIP, sub=BOB.sub, role=role)


def _client(
    monkeypatch: pytest.MonkeyPatch,
    role: TripRole = TripRole.CO_HOST,
    grants: tuple[Grant, ...] | None = None,
) -> TestClient:
    def get_membership(
        _session: object, _trip: uuid.UUID, _sub: str, min_role: TripRole
    ) -> TripMembership:
        if not role.satisfies(min_role):
            msg = f"Trip role '{min_role}' required (you are '{role}')"
            raise trip_service.TripRoleError(msg)
        return _membership(role)

    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(side_effect=get_membership)
    )
    app = create_app()
    if grants is None:
        authorize(app, BOB)
    else:
        authorize(app, BOB, grants)
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


def test_amenity_keys_are_the_places_vocabulary() -> None:
    assert KEYS[RequirementKind.AMENITY] is Amenity
    assert all(is_known_key(RequirementKind.AMENITY, a.value) for a in Amenity)
    assert is_known_key(RequirementKind.PLATFORM, "airbnb")
    assert is_known_key(RequirementKind.DISTANCE, "attractions")
    assert not is_known_key(RequirementKind.AMENITY, "airbnb")


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Unknown amenity key"):
        RequirementItem(kind=RequirementKind.AMENITY, key="sauna", hard=True)


def test_distance_needs_max_distance_and_only_distance_may_have_it() -> None:
    with pytest.raises(ValidationError):
        RequirementItem(kind=RequirementKind.DISTANCE, key="attractions", hard=False)
    with pytest.raises(ValidationError):
        RequirementItem(
            kind=RequirementKind.AMENITY, key="pool", hard=True, max_distance_m=500
        )
    item = RequirementItem(
        kind=RequirementKind.DISTANCE, key="attractions", hard=False, max_distance_m=800
    )
    assert item.max_distance_m == 800


def test_duplicate_kind_and_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="only once"):
        RequirementsWrite.model_validate({"requirements": [POOL, POOL]})


def test_put_rejects_unknown_key_with_422(monkeypatch: pytest.MonkeyPatch) -> None:
    with _client(monkeypatch) as client:
        body = {"requirements": [{**POOL, "key": "sauna"}]}
        response = client.put(path("put_requirements", trip_id=TRIP), json=body)
        assert response.status_code == 422


def test_member_may_read_but_not_write(monkeypatch: pytest.MonkeyPatch) -> None:
    read = AsyncMock(return_value=RequirementsRead(requirements=[], version=0))
    monkeypatch.setattr(requirements_service, "get_requirements", read)
    with _client(monkeypatch, TripRole.MEMBER) as client:
        assert client.get(path("get_requirements", trip_id=TRIP)).status_code == 200
        put = client.put(
            path("put_requirements", trip_id=TRIP), json={"requirements": [POOL]}
        )
        assert put.status_code == 403


def test_write_needs_the_accommodation_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    grants = (Grant(Feature.ACCOMMODATION, Access.READ),)
    with _client(monkeypatch, TripRole.HOST, grants) as client:
        response = client.put(
            path("put_requirements", trip_id=TRIP), json={"requirements": []}
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Missing permission accommodation:WRITE"


def test_non_member_gets_404(monkeypatch: pytest.MonkeyPatch) -> None:
    error = TripNotFoundError(str(TRIP))
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=error))
    app = create_app()
    authorize(app, BOB)
    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app) as client:
        assert client.get(path("get_requirements", trip_id=TRIP)).status_code == 404
        put = client.put(
            path("put_requirements", trip_id=TRIP), json={"requirements": []}
        )
        assert put.status_code == 404


class _Store:
    """In-memory stand-in for ``accommodation.db``."""

    def __init__(self) -> None:
        self.rows: list[Any] = []
        self.version = 0

    async def select_requirements(self, _s: object, _t: uuid.UUID) -> list[Any]:
        return self.rows

    async def select_version(self, _s: object, _t: uuid.UUID) -> int:
        return self.version

    async def replace_requirements(
        self, _s: object, _t: uuid.UUID, rows: list[Any]
    ) -> None:
        self.rows = rows

    async def bump_version(self, _s: object, _t: uuid.UUID) -> None:
        self.version += 1


class _Session:
    commits = 0

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    fake = _Store()
    for name in (
        "select_requirements",
        "select_version",
        "replace_requirements",
        "bump_version",
    ):
        monkeypatch.setattr(db, name, getattr(fake, name))
    return fake


def _put(
    monkeypatch: pytest.MonkeyPatch,
    trip: TripRead,
    items: list[dict[str, Any]],
) -> RequirementsRead:
    monkeypatch.setattr(trip_service, "get_trip", AsyncMock(return_value=trip))
    return asyncio.run(
        requirements_service.replace_requirements(
            _Session(),  # ty: ignore[invalid-argument-type]
            _membership(TripRole.CO_HOST),
            RequirementsWrite.model_validate({"requirements": items}),
        )
    )


def test_outing_rejects_requirements(
    monkeypatch: pytest.MonkeyPatch, store: _Store
) -> None:
    day = date(2026, 11, 1)
    with pytest.raises(RequirementsInvalidError, match="outing"):
        _put(monkeypatch, _trip(day, day), [POOL])
    assert store.version == 0


def test_version_moves_only_on_a_real_change(
    monkeypatch: pytest.MonkeyPatch, store: _Store
) -> None:
    trip = _trip(date(2026, 11, 1), date(2026, 11, 4))
    first = _put(monkeypatch, trip, [POOL])
    assert first.version == 1
    store.rows = [type("Row", (), {**POOL, "max_distance_m": None})()]
    again = _put(monkeypatch, trip, [POOL])
    assert again.version == 1
    changed = _put(monkeypatch, trip, [{**POOL, "hard": False}])
    assert changed.version == 2
