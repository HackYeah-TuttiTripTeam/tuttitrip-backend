"""Places catalog: provenance marks, permissions, schema rules."""

import io
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, UniqueConstraint

from tests.shared.paths import path
from tests.shared.tokens import bearer, make_verifier
from tuttitrip.main import create_app
from tuttitrip.places import db
from tuttitrip.places.models import City, Place, PlacePrice, TransitFare
from tuttitrip.places.schemas import CityRead, OpeningHours, PlaceRead, PlaceTag
from tuttitrip.shared.auth.api import get_token_verifier
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.api import get_user_grants
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature

ROOT = Path(__file__).resolve().parents[2]
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
        "source_key": None,
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
        "indoor": None,
        "iconic": True,
        "unique_experience": False,
        "cuisine": None,
        "diet_tags": [],
        "amenities": [],
        "source": "sheet",
    }
    place = Place(**{**fields, **overrides})
    place.prices = [
        PlacePrice(
            ticket_category="adult",
            unit="person",
            age_min=None,
            age_max=None,
            family_size=None,
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
            "unit": "person",
            "age_min": None,
            "age_max": None,
            "family_size": None,
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
    assert select.call_args.kwargs == {"limit": 100, "offset": 0}


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


def test_unknown_indoor_stays_unknown() -> None:
    assert PlaceRead.model_validate(make_place()).indoor is None


def test_google_place_id_is_kept() -> None:
    read = PlaceRead.model_validate(make_place(google_place_id="ChIJabc"))
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


def test_list_pages_are_bounded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    select = AsyncMock(return_value=[])
    monkeypatch.setattr(db, "select_places", select)
    params = {"city": "krakow", "limit": 20, "offset": 40}
    assert client.get(path("list_places"), params=params, headers=bearer()).json() == []
    assert select.call_args.kwargs == {"limit": 20, "offset": 40}
    for bad in ({"limit": 0}, {"limit": 501}, {"offset": -1}):
        response = client.get(
            path("list_places"), params={"city": "krakow", **bad}, headers=bearer()
        )
        assert response.status_code == 422


def test_cities_endpoint_and_time_zone_validation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    city = City(
        slug="london",
        name="Londyn",
        country="GB",
        timezone="Europe/London",
        currency="GBP",
        center_lat=51.5,
        center_lon=-0.12,
        bbox_south=51.3,
        bbox_west=-0.5,
        bbox_north=51.7,
        bbox_east=0.3,
    )
    monkeypatch.setattr(db, "select_cities", AsyncMock(return_value=[city]))
    body = client.get(path("list_cities"), headers=bearer()).json()
    assert body[0]["timezone"] == "Europe/London"
    city.timezone = "Mars/Olympus"
    with pytest.raises(ValidationError):
        CityRead.model_validate(city)


def test_bad_values_are_rejected_by_the_dto_not_by_a_crash() -> None:
    with pytest.raises(ValidationError):
        PlaceRead.model_validate(make_place(tags=["history", "nope"]))
    with pytest.raises(ValidationError):
        PlaceRead.model_validate(make_place(diet_tags=["paleo"]))
    with pytest.raises(ValidationError):
        PlaceRead.model_validate(make_place(cuisine="martian"))


def _checks(model: type[Place | PlacePrice | TransitFare | City]) -> dict[str, str]:
    return {
        str(c.name): str(c.sqltext)
        for c in model.__table__.constraints  # ty: ignore[unresolved-attribute]
        if isinstance(c, CheckConstraint)
    }


@pytest.mark.parametrize(
    ("model", "name", "needle"),
    [
        (Place, "ck_places_hours_provenance", "NOT hours_verified OR (opening_hours"),
        (PlacePrice, "ck_place_prices_provenance", "NOT verified OR (source_url"),
        (TransitFare, "ck_transit_fares_provenance", "NOT verified OR (source_url"),
        (Place, "ck_places_tags", "tags <@ ARRAY['history'"),
        (Place, "ck_places_diet_tags", "diet_tags <@ ARRAY['vegetarian'"),
        (Place, "ck_places_amenities", "amenities <@ ARRAY['pool'"),
        (Place, "ck_places_cuisine", "cuisine IN ('polish'"),
        (City, "ck_cities_slug", "^[a-z0-9-]+$"),
        (City, "ck_cities_country", "^[A-Z]{2}$"),
        (PlacePrice, "ck_place_prices_currency", "^[A-Z]{3}$"),
        (PlacePrice, "ck_place_prices_unit", "'person', 'night', 'group'"),
    ],
)
def test_database_checks_guard_provenance_and_vocabulary(
    model: type[Place | PlacePrice | TransitFare | City], name: str, needle: str
) -> None:
    assert needle in _checks(model)[name]


def test_sheet_import_has_an_idempotent_key() -> None:
    unique = {
        tuple(column.name for column in c.columns)
        for c in Place.__table__.constraints  # ty: ignore[unresolved-attribute]
        if isinstance(c, UniqueConstraint)
    }
    assert ("source", "source_key") in unique


def test_migration_sql_creates_the_checks_unique_keys_and_user_grant() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    buffer = io.StringIO()
    config.output_buffer = buffer
    command.upgrade(config, "head", sql=True)
    sql = " ".join(buffer.getvalue().split())
    assert "UNIQUE (osm_type, osm_id)" in sql
    assert "UNIQUE (source, source_key)" in sql
    assert "NOT hours_verified OR (opening_hours IS NOT NULL" in sql
    assert "CONSTRAINT ck_place_prices_provenance CHECK" in sql
    assert "CONSTRAINT ck_transit_fares_provenance CHECK" in sql
    assert "tags <@ ARRAY[" in sql
    assert (
        "INSERT INTO role_grants (role_name, feature, level) "
        "VALUES ('user', 'places.catalog', 'READ') ON CONFLICT DO NOTHING"
    ) in sql
