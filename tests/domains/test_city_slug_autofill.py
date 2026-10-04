"""A trip made from the UI gets its ``city_slug`` from the destination (#224)."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from tuttitrip.places.cities.logic.resolve import city_slug_for
from tuttitrip.trips.schemas import MemberStatus, TripCreate, TripMembership, TripRole
from tuttitrip.trips.services import trip_service

CATALOG = [("krakow", "Kraków"), ("warszawa", "Warszawa"), ("gdansk", "Gdańsk")]


@pytest.mark.parametrize(
    ("destination", "slug"),
    [
        ("Kraków", "krakow"),
        ("KRAKOW", "krakow"),
        ("  warszawa ", "warszawa"),
        ("Gdańsk, Polska", "gdansk"),
        ("Łódź", "lodz"),
        ("Gdynia, Polska", "gdynia-polska"),
        ("???", None),
        ("", None),
    ],
)
def test_destination_gets_slug(destination: str, slug: str | None) -> None:
    assert city_slug_for(destination, CATALOG) == slug


def _stored(destination: str | None, city_slug: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        owner_sub="auth0|x",
        name="Wyjazd",
        destination=destination,
        city_slug=city_slug,
        created_at=datetime.now(UTC),
        is_sample=False,
    )


def _patch(monkeypatch: pytest.MonkeyPatch, slug: str | None) -> None:
    monkeypatch.setattr(
        trip_service.place_service, "find_city_slug", AsyncMock(return_value=slug)
    )
    monkeypatch.setattr(
        trip_service.TripDetails,
        "model_validate",
        lambda *_a, **_k: MagicMock(model_dump=lambda **_: {}),
    )
    monkeypatch.setattr(trip_service, "TripRead", lambda **_: "read")


def _member(trip: SimpleNamespace) -> TripMembership:
    return TripMembership(
        trip_id=trip.id,
        sub="auth0|x",
        role=TripRole.HOST,
        status=MemberStatus.CONFIRMED,
    )


def _create(monkeypatch: pytest.MonkeyPatch, data: TripCreate) -> dict[str, object]:
    _patch(monkeypatch, "warszawa")
    insert = AsyncMock(return_value=_stored(data.destination, None))
    monkeypatch.setattr(trip_service.db, "insert_trip", insert)
    monkeypatch.setattr(
        trip_service.profile_service, "create_host_profile", AsyncMock()
    )
    asyncio.run(trip_service.create_trip(AsyncMock(), "auth0|x", data))
    call = insert.await_args
    assert call is not None
    return dict(call.kwargs["fields"])


def test_create_sets_city_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    data = TripCreate(name="Wyjazd do Warszawy", destination="Warszawa")
    fields = _create(monkeypatch, data)
    assert fields["city_slug"] == "warszawa"


def test_create_keeps_chosen_city(monkeypatch: pytest.MonkeyPatch) -> None:
    data = TripCreate(name="Wyjazd", destination="Warszawa", city_slug="krakow")
    fields = _create(monkeypatch, data)
    assert fields["city_slug"] == "krakow"


def _fill(
    monkeypatch: pytest.MonkeyPatch, destination: str, slug: str | None
) -> tuple[SimpleNamespace, AsyncMock]:
    _patch(monkeypatch, slug)
    trip = _stored(destination, None)
    monkeypatch.setattr(trip_service.db, "select_trip", AsyncMock(return_value=trip))
    session = AsyncMock()
    asyncio.run(trip_service.fill_city_slug(session, _member(trip)))
    return trip, session


def test_fill_city_slug_saves_match(monkeypatch: pytest.MonkeyPatch) -> None:
    trip, session = _fill(monkeypatch, "Kraków", "krakow")
    assert trip.city_slug == "krakow"
    session.commit.assert_awaited_once()


def test_fill_city_slug_leaves_trip_without_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, session = _fill(monkeypatch, "", None)
    assert trip.city_slug is None
    session.commit.assert_not_awaited()
