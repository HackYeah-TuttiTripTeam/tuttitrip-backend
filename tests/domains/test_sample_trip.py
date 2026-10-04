"""The sample trip: language, dates, content and the guards that need no database."""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest

from tuttitrip.demo import db as demo_db
from tuttitrip.demo.logic.sample_trip import (
    NIGHTS,
    first_friday,
    locale_from_header,
    sample_trip,
)
from tuttitrip.demo.services import sample_trip_service
from tuttitrip.shared.config.settings import SampleTripSettings, Settings

FRIDAY = 4


@pytest.mark.parametrize(
    ("header", "locale"),
    [
        (None, "pl"),
        ("", "pl"),
        ("pl-PL,pl;q=0.9,en;q=0.8", "pl"),
        ("en", "en"),
        ("en-GB,en;q=0.8", "en"),
        ("EN-us", "en"),
        ("de-DE,en;q=0.5", "pl"),
        ("*", "pl"),
    ],
)
def test_locale_follows_the_first_language_of_the_header(
    header: str | None, locale: str
) -> None:
    assert locale_from_header(header) == locale


@pytest.mark.parametrize("offset", range(14))
def test_the_trip_starts_on_the_first_friday_two_weeks_ahead(offset: int) -> None:
    today = date(2026, 10, 1) + timedelta(days=offset)
    start = first_friday(today)
    assert start.weekday() == FRIDAY
    assert timedelta(days=14) <= start - today < timedelta(days=21)


def test_the_content_is_the_same_for_the_same_day_and_labelled_in_both_languages() -> (
    None
):
    today = date(2026, 10, 4)
    pl = sample_trip("pl", "warszawa", today)
    assert pl == sample_trip("pl", "warszawa", today)
    en = sample_trip("en", "warszawa", today)
    assert pl.trip.name.startswith("Przykład: ")
    assert en.trip.name.startswith("Sample: ")
    assert pl.trip.starts_in_days == en.trip.starts_in_days
    assert pl.trip.nights == NIGHTS
    for content in (pl, en):
        names = {p.name for p in content.trip.people}
        assert len(names) == len(content.trip.people) == 4
        for expense in content.expenses:
            assert {expense.payer, *expense.participants} <= names
    assert {p.name for p in en.trip.people} != {p.name for p in pl.trip.people}


def test_the_sample_is_on_by_default_and_documented() -> None:
    settings = Settings().sample_trip
    assert settings.enabled is True
    assert settings.city_slug == "warszawa"
    assert SampleTripSettings.__doc__


def test_a_failure_never_breaks_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(demo_db, "has_sample_grant", AsyncMock(return_value=False))
    broken = AsyncMock(side_effect=RuntimeError("solver"))
    monkeypatch.setattr(sample_trip_service, "_in_transaction", broken)
    assert (
        asyncio.run(sample_trip_service.ensure_sample_trip(AsyncMock(), "auth0|x"))
        is None
    )
    broken.assert_awaited_once()


def test_an_account_with_the_mark_costs_one_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(demo_db, "has_sample_grant", AsyncMock(return_value=True))
    creating = AsyncMock()
    monkeypatch.setattr(sample_trip_service, "_in_transaction", creating)
    assert (
        asyncio.run(sample_trip_service.ensure_sample_trip(AsyncMock(), "auth0|x"))
        is None
    )
    creating.assert_not_awaited()


def test_a_disabled_sample_never_looks_at_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TUTTITRIP_SAMPLE_TRIP__ENABLED", "false")
    sample_trip_service.get_settings.cache_clear()
    try:
        lookup = AsyncMock()
        monkeypatch.setattr(demo_db, "has_sample_grant", lookup)
        ensured = sample_trip_service.ensure_sample_trip(None, "auth0|x")  # ty: ignore[invalid-argument-type]
        assert asyncio.run(ensured) is None
        lookup.assert_not_awaited()
    finally:
        sample_trip_service.get_settings.cache_clear()
