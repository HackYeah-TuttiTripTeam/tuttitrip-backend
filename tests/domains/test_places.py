"""Places catalog: provenance marks, permissions, schema rules."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.shared.paths import path
from tests.shared.tokens import bearer, make_verifier
from tuttitrip.main import create_app
from tuttitrip.places import db
from tuttitrip.places.models import Place, PlacePrice
from tuttitrip.places.schemas import OpeningHours, PlaceTag
from tuttitrip.places.services import place_service
from tuttitrip.shared.auth.api import get_token_verifier
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.api import get_user_grants
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature

CHECKED = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
SOURCE = "https://example.test/cennik"


def make_place(**overrides: Any) -> Place:  # ruff: ignore[any-type]
    """A sheet-sourced place with a verified price and no hours."""
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "city_slug": "krakow",
        "name": "Wawel",
        "category": "attraction",
        "tags": ["history", "architecture"],
        "lat": 50.054,
        "lon": 19.935,
        "osm_type": "way",
        "osm_id": 1,
        "google_place_id": None,
        "opening_hours": None,
        "hours_source_url": None,
        "hours_verified": False,
        "hours_checked_at": None,
        "typical_visit_min": 120,
        "segment_km": 1.2,
        "transfer_min": 10,
        "queue_min": 20,
        "stairs": 0.4,
        "wheelchair": None,
        "indoor": False,
        "iconic": True,
        "cuisine": None,
        "diet_tags": [],
        "source": "sheet",
    }
    place = Place(**{**fields, **overrides})
    place.prices = [
        PlacePrice(
            ticket_category="adult",
            amount=Decimal("35.00"),
            currency="PLN",
            source_url=SOURCE,
            verified=True,
            checked_at=CHECKED,
        )
    ]
    return place


@pytest.fixture
def app() -> FastAPI:
    application = create_app()
    verifier = make_verifier()
    application.dependency_overrides[get_token_verifier] = lambda: verifier
    application.dependency_overrides[get_session] = lambda: None
    application.dependency_overrides[get_user_grants] = lambda: [
        Grant(Feature.PLACES_CATALOG, Access.READ)
    ]
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_price_is_verified_and_hours_are_not(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    place = make_place()
    monkeypatch.setattr(db, "select_place", AsyncMock(return_value=place))
    response = client.get(path("get_place", place_id=place.id), headers=bearer())
    assert response.status_code == 200
    body = response.json()
    assert body["prices"] == [
        {
            "ticket_category": "adult",
            "amount": "35.00",
            "currency": "PLN",
            "source_url": SOURCE,
            "verified": True,
            "checked_at": "2026-09-30T12:00:00Z",
        }
    ]
    assert body["hours"] == {
        "opening_hours": None,
        "source_url": None,
        "verified": False,
        "checked_at": None,
    }
    assert body["tags"] == ["history", "architecture"]


def test_weekly_hours_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    hours = {
        "weekly": {"tue": [{"open": "09:00", "close": "17:00"}]},
        "closed_dates": ["2026-12-25"],
    }
    place = make_place(
        opening_hours=hours,
        hours_source_url=SOURCE,
        hours_verified=True,
        hours_checked_at=CHECKED,
    )
    monkeypatch.setattr(db, "select_place", AsyncMock(return_value=place))
    body = client.get(path("get_place", place_id=place.id), headers=bearer()).json()
    assert body["hours"]["verified"] is True
    assert body["hours"]["opening_hours"] == hours


def test_unknown_place_is_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "select_place", AsyncMock(return_value=None))
    response = client.get(path("get_place", place_id=uuid.uuid4()), headers=bearer())
    assert response.status_code == 404


def test_list_filters_by_city_and_category(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    select = AsyncMock(return_value=[make_place()])
    monkeypatch.setattr(db, "select_places", select)
    response = client.get(
        path("list_places"),
        params={"city": "krakow", "category": "attraction"},
        headers=bearer(),
    )
    assert response.status_code == 200
    assert [p["name"] for p in response.json()] == ["Wawel"]
    assert select.call_args.args[1:] == ("krakow", "attraction")


def test_list_requires_a_city_and_a_known_category(client: TestClient) -> None:
    assert client.get(path("list_places"), headers=bearer()).status_code == 422
    params = {"city": "krakow", "category": "nope"}
    response = client.get(path("list_places"), params=params, headers=bearer())
    assert response.status_code == 422


def test_catalog_needs_a_token(client: TestClient) -> None:
    response = client.get(path("list_places"), params={"city": "krakow"})
    assert response.status_code == 401


def test_catalog_needs_the_permission(app: FastAPI) -> None:
    app.dependency_overrides[get_user_grants] = lambda: [
        Grant(Feature.TRIPS_CORE, Access.READ)
    ]
    response = TestClient(app).get(
        path("list_places"), params={"city": "krakow"}, headers=bearer()
    )
    assert response.status_code == 403
    assert "places.catalog:READ" in response.json()["detail"]


def test_default_user_role_reads_the_catalog() -> None:
    # The migration seeds ('user', 'places.catalog', 'READ'); the node must exist.
    assert Feature("places.catalog").parent is Feature.PLACES


def test_osm_id_is_unique_only_together_with_the_type() -> None:
    unique = [
        sorted(column.name for column in constraint.columns)
        for constraint in Place.__table__.constraints  # ty: ignore[unresolved-attribute]
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]
    assert ["osm_id", "osm_type"] in unique


def test_to_read_keeps_google_place_id() -> None:
    read = place_service.to_read(make_place(google_place_id="ChIJabc"))
    assert read.google_place_id == "ChIJabc"


def test_opening_hours_reject_bad_intervals() -> None:
    with pytest.raises(ValidationError):
        OpeningHours.model_validate(
            {"weekly": {"mon": [{"open": "9:00", "close": "17:00"}]}}
        )
    with pytest.raises(ValidationError):
        OpeningHours.model_validate(
            {"weekly": {"mon": [{"open": "18:00", "close": "17:00"}]}}
        )
    with pytest.raises(ValidationError):
        OpeningHours.model_validate({"weekly": {"funday": []}})
    ok = OpeningHours.model_validate(
        {"weekly": {"mon": [{"open": "00:00", "close": "24:00"}]}}
    )
    assert ok.closed_dates == []


def test_tags_are_a_stable_taxonomy() -> None:
    assert PlaceTag("local_food") is PlaceTag.LOCAL_FOOD
    assert len({tag.value for tag in PlaceTag}) == len(PlaceTag)
