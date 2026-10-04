"""Export of a plan version to an iCalendar file (RFC 5545).

One ``VEVENT`` per stop. Times are in the zone of the city (``TZID`` plus a
``VTIMEZONE`` made from the IANA database), so every calendar shows the local
hours. The file is reproducible: ``UID`` is a UUIDv5 of the plan version and the
stop, ``DTSTAMP`` is the time the version was stored (never the clock) and
``SEQUENCE`` is the version number. Text escaping and line folding are done by
``icalendar``. Pure.
"""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

from tuttitrip.planning.plans.schemas import PlanRead, PlanStop

UID_DOMAIN = "tuttitrip.gburek.app"
"""Namespace of the ``UID`` values (``uuid5`` of this DNS name)."""
PRODID = "-//TuttiTrip//Plan//PL"
ICS_VERSION = "2.0"
_UID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, UID_DOMAIN)


def event_uid(plan_id: uuid.UUID, day: int, position: int) -> uuid.UUID:
    """Stable ``UID`` of one stop of one plan version.

    Args:
        plan_id: Id of the stored plan version.
        day: 1-based day number.
        position: 0-based position of the stop in the day.

    Returns:
        The same value for the same version and place in it.
    """
    return uuid.uuid5(_UID_NAMESPACE, f"{plan_id}:{day}:{position}")


def _description(stop: PlanStop, currency: str) -> str:
    lines: list[str] = []
    if stop.cost_per_person is None:
        lines.append("Cena: brak danych")
    else:
        lines.append(f"Cena: {stop.cost_per_person:.2f} {currency} za osobę")
    if stop.price_verified:
        lines.append("Cena zweryfikowana")
    else:
        lines.append("Cena niezweryfikowana")
    lines.append(f"Źródło ceny: {stop.price_source_url or 'brak'}")
    if not stop.hours_verified:
        lines.append("Godziny niezweryfikowane")
    return "\n".join(lines)


def _event(  # ruff: ignore[too-many-arguments] one stop and the plan's context
    plan: PlanRead,
    zone: ZoneInfo,
    *,
    day: int,
    date: dt.date,
    position: int,
    stop: PlanStop,
) -> Event:
    start = dt.datetime.combine(date, stop.start, tzinfo=zone)
    end = dt.datetime.combine(date, stop.end, tzinfo=zone)
    if end < start:  # a stop that runs past midnight
        end += dt.timedelta(days=1)
    event = Event.new(
        uid=event_uid(plan.id, day, position),
        stamp=plan.created_at,
        sequence=plan.version,
        start=start,
        end=end,
        summary=stop.name,
        location=stop.address or stop.name,
        description=_description(stop, plan.budget.currency),
    )
    event.add("geo", (stop.lat, stop.lon))
    return event


def build_ics(plan: PlanRead, timezone: str, calendar_name: str) -> bytes:
    """Render a plan version as an ``.ics`` file.

    Args:
        plan: The stored version.
        timezone: IANA zone of the city; the hours of the stops are local to it.
        calendar_name: Shown by clients as the calendar's name.

    Returns:
        The file, CRLF-separated and folded to 75 octets; the same plan gives the
        same bytes.
    """
    zone = ZoneInfo(timezone)
    calendar = Calendar.new(
        prodid=PRODID, version=ICS_VERSION, name=calendar_name, uid=plan.id
    )
    for day in plan.days:
        if day.date is None:
            continue
        for position, stop in enumerate(day.items):
            calendar.add_component(
                _event(
                    plan,
                    zone,
                    day=day.index,
                    date=day.date,
                    position=position,
                    stop=stop,
                )
            )
    calendar.add_missing_timezones()
    return bytes(calendar.to_ical())
