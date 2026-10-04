"""Calendar events and the Drive page of an approved plan (pure)."""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

from tests.domains.test_ics import ZONE, dated_plan
from tuttitrip.planning.exports.logic.calendar_events import build_events, event_id
from tuttitrip.planning.exports.logic.plan_html import build_html


def test_every_stop_is_an_event_with_a_stable_valid_id() -> None:
    plan = dated_plan()
    events = build_events(plan, ZONE)
    assert len(events) == sum(len(d.items) for d in plan.days) == 8
    assert len({e.id for e in events}) == len(events)
    for event in events:
        assert set(event.id) <= set("0123456789abcdef")  # base32hex characters
        assert 5 <= len(event.id) <= 1024
    assert [e.id for e in events] == [e.id for e in build_events(plan, ZONE)]


def test_a_newer_version_of_the_trip_addresses_the_same_events() -> None:
    plan = dated_plan()
    newer = plan.model_copy(update={"id": uuid.uuid4(), "version": plan.version + 1})
    assert [e.id for e in build_events(newer, ZONE)] == [
        e.id for e in build_events(plan, ZONE)
    ]
    assert event_id(plan.trip_id, 1, 0) != event_id(uuid.uuid4(), 1, 0)


def test_times_are_local_to_the_city() -> None:
    plan = dated_plan()
    first = plan.days[0].items[0]
    first_day = plan.days[0].date
    assert first_day is not None
    body = build_events(plan, ZONE)[0].body
    assert body["summary"] == first.name
    assert body["start"] == {
        "dateTime": dt.datetime.combine(first_day, first.start).isoformat(),
        "timeZone": ZONE,
    }
    assert ZoneInfo(body["end"]["timeZone"])


def test_a_stop_past_midnight_ends_the_next_day() -> None:
    plan = dated_plan()
    late = (
        plan.days[0]
        .items[0]
        .model_copy(update={"start": dt.time(23, 0), "end": dt.time(1, 0)})
    )
    days = [plan.days[0].model_copy(update={"items": [late]}), *plan.days[1:]]
    body = build_events(plan.model_copy(update={"days": days}), ZONE)[0].body
    first_day = plan.days[0].date
    assert first_day is not None
    assert body["end"]["dateTime"].startswith(
        (first_day + dt.timedelta(days=1)).isoformat()
    )


def test_days_without_a_date_have_no_events() -> None:
    plan = dated_plan()
    undated = plan.model_copy(
        update={"days": [d.model_copy(update={"date": None}) for d in plan.days]}
    )
    assert build_events(undated, ZONE) == []


def test_the_page_lists_the_days_and_escapes_html() -> None:
    plan = dated_plan()
    first = plan.days[0].items[0].model_copy(update={"name": "<script>x</script> & Co"})
    days = [plan.days[0].model_copy(update={"items": [first]}), *plan.days[1:]]
    html = build_html(plan.model_copy(update={"days": days}), "Gdańsk <3")
    assert "<script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt; &amp; Co" in html
    assert "Gdańsk &lt;3" in html
    assert html.count("<h2>") == len(plan.days)
    assert html.startswith("<!DOCTYPE html>")
