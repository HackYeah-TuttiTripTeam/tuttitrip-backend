"""ICS export of a plan version (RFC 5545): escaping, folding, zone, reproducibility."""

import datetime as dt
import uuid
from typing import Any

import pytest
from icalendar import Calendar

from tuttitrip.planning.plans.logic.ics import PRODID, build_ics, event_uid
from tuttitrip.planning.plans.logic.sample_plan import sample_plan
from tuttitrip.planning.plans.schemas import PlanRead

TRIP = uuid.UUID("00000000-0000-4000-8000-000000000042")
FIRST_DAY = dt.date(2026, 10, 10)
ZONE = "Europe/Warsaw"


def dated_plan() -> PlanRead:
    """The sample plan of three days with dates (the sample has none)."""
    plan = sample_plan(TRIP)
    days = [
        day.model_copy(update={"date": FIRST_DAY + dt.timedelta(days=i)})
        for i, day in enumerate(plan.days)
    ]
    return plan.model_copy(update={"days": days})


def events(data: bytes) -> list[dict[str, Any]]:
    calendar = Calendar.from_ical(data)
    return [dict(e) for e in calendar.walk("VEVENT")]


def test_the_same_plan_gives_the_same_bytes() -> None:
    plan = dated_plan()
    assert build_ics(plan, ZONE, "Plan") == build_ics(plan, ZONE, "Plan")


def test_the_file_is_a_valid_calendar_with_an_event_per_stop() -> None:
    plan = dated_plan()
    data = build_ics(plan, ZONE, "Plan")
    calendar = Calendar.from_ical(data)
    assert calendar["PRODID"] == PRODID
    assert calendar["VERSION"] == "2.0"
    stops = sum(len(d.items) for d in plan.days)
    assert len(events(data)) == stops == 8
    for event in events(data):
        assert {"UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY"} <= set(event)


def test_lines_are_crlf_and_at_most_75_octets() -> None:
    plan = dated_plan()
    long_name = "Muzeum, które ma bardzo długą nazwę; " * 6
    first = plan.days[0].items[0].model_copy(update={"name": long_name})
    days = [plan.days[0].model_copy(update={"items": [first]}), *plan.days[1:]]
    data = build_ics(plan.model_copy(update={"days": days}), ZONE, "Plan")
    assert b"\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert all(len(line) <= 75 for line in data.split(b"\r\n"))
    # Folded text unfolds to the original, with the specials escaped.
    summary = next(e["SUMMARY"] for e in events(data) if e["SUMMARY"] == long_name)
    assert summary == long_name
    assert rb"\;" in data
    assert rb"\," in data


def test_times_are_local_to_the_city_with_a_timezone_definition() -> None:
    plan = dated_plan()
    data = build_ics(plan, ZONE, "Plan")
    text = data.decode()
    assert "BEGIN:VTIMEZONE" in text
    assert "TZID:Europe/Warsaw" in text
    first = events(data)[0]
    stop = plan.days[0].items[0]
    start = first["DTSTART"].dt
    assert start.replace(tzinfo=None) == dt.datetime.combine(FIRST_DAY, stop.start)
    assert str(start.tzinfo) == ZONE
    assert f"DTSTART;TZID={ZONE}:20261010T" in text


def test_uid_is_stable_per_version_and_stop_and_differs_between_versions() -> None:
    plan = dated_plan()
    uids = [e["UID"] for e in events(build_ics(plan, ZONE, "Plan"))]
    assert len(set(uids)) == len(uids)
    assert uids[0] == str(event_uid(plan.id, 1, 0))
    other = plan.model_copy(update={"id": uuid.uuid4()})
    other_uids = [e["UID"] for e in events(build_ics(other, ZONE, "Plan"))]
    assert not set(uids) & set(other_uids)


def test_dtstamp_is_the_time_the_version_was_stored_and_sequence_its_number() -> None:
    plan = dated_plan().model_copy(update={"version": 4})
    for event in events(build_ics(plan, ZONE, "Plan")):
        assert event["DTSTAMP"].dt == plan.created_at
        assert event["SEQUENCE"] == 4


def test_an_unverified_price_says_so_and_names_the_source() -> None:
    plan = dated_plan()
    stop = (
        plan.days[0]
        .items[0]
        .model_copy(
            update={
                "price_verified": False,
                "price_source_url": "https://src.invalid/p",
            }
        )
    )
    days = [plan.days[0].model_copy(update={"items": [stop]}), *plan.days[1:]]
    data = build_ics(plan.model_copy(update={"days": days}), ZONE, "Plan")
    description = str(events(data)[0]["DESCRIPTION"])
    assert "Cena niezweryfikowana" in description
    assert "https://src.invalid/p" in description


def test_a_verified_price_and_a_missing_one() -> None:
    plan = dated_plan()
    shown = str(events(build_ics(plan, ZONE, "Plan"))[0]["DESCRIPTION"])
    assert "Cena zweryfikowana" in shown
    assert "PLN za osobę" in shown
    free = (
        plan.days[0]
        .items[0]
        .model_copy(update={"cost_per_person": None, "price_source_url": None})
    )
    days = [plan.days[0].model_copy(update={"items": [free]}), *plan.days[1:]]
    text = str(
        events(build_ics(plan.model_copy(update={"days": days}), ZONE, "Plan"))[0][
            "DESCRIPTION"
        ]
    )
    assert "Cena: brak danych" in text
    assert "Źródło ceny: brak" in text


def test_a_stop_past_midnight_ends_the_next_day() -> None:
    plan = dated_plan()
    late = (
        plan.days[0]
        .items[0]
        .model_copy(update={"start": dt.time(22, 30), "end": dt.time(0, 30)})
    )
    days = [plan.days[0].model_copy(update={"items": [late]}), *plan.days[1:]]
    event = events(build_ics(plan.model_copy(update={"days": days}), ZONE, "Plan"))[0]
    assert event["DTEND"].dt - event["DTSTART"].dt == dt.timedelta(hours=2)


def test_days_without_a_date_are_skipped() -> None:
    plan = sample_plan(TRIP)
    assert events(build_ics(plan, ZONE, "Plan")) == []


@pytest.mark.parametrize("zone", ["Europe/Berlin", "America/New_York"])
def test_other_zones(zone: str) -> None:
    data = build_ics(dated_plan(), zone, "Plan")
    assert f"TZID:{zone}".encode() in data
