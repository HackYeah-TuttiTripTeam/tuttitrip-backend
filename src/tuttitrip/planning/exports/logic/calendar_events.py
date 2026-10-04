"""Calendar events of an approved plan, in the shape of the Google Calendar API.

The id of an event is a UUIDv5 of the trip, the day and the position of the stop
(its hex digits are valid base32hex), so saving the same plan again, or a newer
version of it, addresses the same events instead of adding new ones. Pure.
"""

import datetime as dt
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from tuttitrip.planning.exports.logic.constants import EVENT_ID_NAMESPACE
from tuttitrip.planning.plans.logic.ics import stop_description
from tuttitrip.planning.plans.schemas import PlanRead


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """One event: its id and the resource to send."""

    id: str
    body: dict[str, Any]


def event_id(trip_id: UUID, day: int, position: int) -> str:
    """Stable id of the event of one stop.

    Args:
        trip_id: Trip id.
        day: 1-based day number.
        position: 0-based position of the stop in the day.

    Returns:
        32 hex digits.
    """
    return uuid5(EVENT_ID_NAMESPACE, f"{trip_id}:{day}:{position}").hex


def _moment(date: dt.date, time: dt.time, zone: ZoneInfo) -> dict[str, str]:
    start = dt.datetime.combine(date, time, tzinfo=zone)
    return {"dateTime": start.replace(tzinfo=None).isoformat(), "timeZone": zone.key}


def build_events(plan: PlanRead, timezone: str) -> list[CalendarEvent]:
    """The events of every stop of a plan version that has dates.

    Args:
        plan: The stored version.
        timezone: IANA zone of the city; the hours of the stops are local to it.

    Returns:
        One event per stop, in the order of the plan.
    """
    zone = ZoneInfo(timezone)
    events: list[CalendarEvent] = []
    for day in plan.days:
        if day.date is None:
            continue
        for position, stop in enumerate(day.items):
            end_date = day.date + dt.timedelta(days=1 if stop.end < stop.start else 0)
            events.append(
                CalendarEvent(
                    id=event_id(plan.trip_id, day.index, position),
                    body={
                        "summary": stop.name,
                        "location": stop.address or stop.name,
                        "description": stop_description(stop, plan.budget.currency),
                        "start": _moment(day.date, stop.start, zone),
                        "end": _moment(end_date, stop.end, zone),
                    },
                )
            )
    return events
