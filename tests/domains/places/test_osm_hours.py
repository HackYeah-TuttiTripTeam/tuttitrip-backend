"""OSM opening_hours subset and closure dates of the city sheet."""

from datetime import date

import pytest

from tuttitrip.places.logic.osm_hours import parse_closed_dates, parse_osm_hours
from tuttitrip.places.schemas import Weekday


def spans(value: str, day: Weekday) -> list[tuple[str, str]]:
    hours = parse_osm_hours(value)
    assert hours is not None
    return [(r.open, r.close) for r in hours.weekly.get(day, [])]


def test_every_day_without_a_weekday_selector() -> None:
    assert spans("10:00-22:00", Weekday.WED) == [("10:00", "22:00")]


def test_ranges_lists_and_later_rules_override() -> None:
    value = "Mo-Fr 09:00-17:00; Sa,Su 10:00-19:00; We off"
    assert spans(value, Weekday.TUE) == [("09:00", "17:00")]
    assert spans(value, Weekday.WED) == []
    assert spans(value, Weekday.SUN) == [("10:00", "19:00")]


def test_days_the_string_does_not_name_are_closed() -> None:
    assert spans("Fr 13:00-18:00", Weekday.MON) == []


def test_wrapping_range_and_several_intervals() -> None:
    assert spans("We-Mo 10:30-18:30", Weekday.SUN) == [("10:30", "18:30")]
    assert spans("We-Mo 10:30-18:30", Weekday.TUE) == []
    assert spans("Mo-Su 00:00-05:00,09:00-24:00", Weekday.FRI) == [
        ("00:00", "05:00"),
        ("09:00", "24:00"),
    ]


def test_24_7_is_open_around_the_clock() -> None:
    assert spans("24/7", Weekday.SAT) == [("00:00", "24:00")]


def test_public_holiday_selector_is_ignored_not_guessed() -> None:
    assert spans("Mo-Sa 11:30-18:00; Su,PH 14:00-18:00", Weekday.SUN) == [
        ("14:00", "18:00")
    ]


def test_hours_past_midnight_are_split_at_midnight() -> None:
    assert spans("Fr 20:00-02:00", Weekday.FRI) == [("20:00", "24:00")]
    assert spans("Fr 20:00-02:00", Weekday.SAT) == [("00:00", "02:00")]


@pytest.mark.parametrize(
    "seasonal",
    [
        "Jan 01-Feb 22 09:00-16:30; Feb 23-Mar 29 09:00-18:00",
        "Mar-Oct 10:00-18:00",
        "Oct 09:00-17:00",
        "Jan-Feb 09:00-16:00; May-Aug Sa,Su,PH 09:00-19:00",
    ],
)
def test_seasonal_hours_stay_unknown(seasonal: str) -> None:
    assert parse_osm_hours(seasonal) is None


@pytest.mark.parametrize(
    "broken",
    ["Xx 10:00-18:00", "Mo 25:00-26:00", "Mo 10:00-10:00", "closed", "Mo 10-18"],
)
def test_malformed_hours_raise(broken: str) -> None:
    with pytest.raises(ValueError, match=r"."):
        parse_osm_hours(broken)


def test_closed_dates_read_exact_dates_and_ranges() -> None:
    value = "poniedziałek; 2026-12-24 do 2026-12-26; 2026-11-01; 2026-11-01"
    assert parse_closed_dates(value) == [
        date(2026, 11, 1),
        date(2026, 12, 24),
        date(2026, 12, 25),
        date(2026, 12, 26),
    ]


def test_partial_closures_are_not_turned_into_full_days() -> None:
    value = (
        "2026-10-05 otwarcie dopiero o 11:00; brak; 2026-12-24 i 2026-12-31 do 16:00"
    )
    assert parse_closed_dates(value) == []


def test_reversed_closure_range_raises() -> None:
    with pytest.raises(ValueError, match="invalid closure range"):
        parse_closed_dates("2026-12-26 do 2026-12-24")
