"""Schedule of one day (docs/algorytm.md, sections 3 E0/E2 and 9).

A heuristic, not a router: the chosen places are put in order of the earliest
closing time (then the opening time, then the id) with the visit time
``tau_p`` and the fixed transfer ``transfer_p`` between them, with no travel
time matrix. It also returns the daily distance ``L_d = sum d_p`` and the active
time ``A_d`` (visits plus transfers) that E2 "tempo" needs, and checks the
hard limit of E0: ``L_d <= 1.5 * D_i`` for every person.

Opening hours are wall-clock times in the city's zone. All arithmetic is done
on UTC instants, so a day with a daylight-saving change keeps real durations;
results are converted back to local time. The module is deterministic.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import StrEnum
from typing import Literal
from uuid import UUID

from tuttitrip.places.schemas import OpeningHours, PlaceRead, Weekday

DAILY_KM_FACTOR = 1.5
"""E0: the day may be at most this many times a person's ``D_i`` long."""

_WEEKDAYS = (
    Weekday.MON,
    Weekday.TUE,
    Weekday.WED,
    Weekday.THU,
    Weekday.FRI,
    Weekday.SAT,
    Weekday.SUN,
)


class InfeasibleCode(StrEnum):
    """Why a day cannot be scheduled."""

    CLOSED = "closed"
    NO_FIT = "no_fit"
    DISTANCE = "distance"


@dataclass(frozen=True, slots=True)
class Person:
    """What the day needs to know about one person (``D_i`` and the nap)."""

    id: UUID
    daily_km: float
    nap_start: time | None = None
    nap_minutes: int = 0


@dataclass(frozen=True, slots=True)
class Lunch:
    """Lunch break that starts inside ``[earliest, latest]`` and lasts ``minutes``."""

    earliest: time
    latest: time
    minutes: int


@dataclass(frozen=True, slots=True)
class DayWindow:
    """The day to schedule: date, zone of the city and the window of the trip."""

    day: date
    timezone: tzinfo
    start: time
    end: time
    lunch: Lunch | None = None


@dataclass(frozen=True, slots=True)
class ScheduledVisit:
    """A place in the day, in local time."""

    place_id: UUID
    start: datetime
    end: datetime
    transfer_min: int
    segment_km: float


@dataclass(frozen=True, slots=True)
class ScheduledBreak:
    """A break in the day, in local time."""

    kind: Literal["lunch", "nap"]
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class DaySchedule:
    """A feasible day with the quantities of E0 and E2."""

    visits: tuple[ScheduledVisit, ...]
    breaks: tuple[ScheduledBreak, ...]
    distance_km: float
    active_min: int


@dataclass(frozen=True, slots=True)
class Infeasible:
    """A day that cannot be scheduled, with the places or people to blame."""

    code: InfeasibleCode
    place_ids: tuple[UUID, ...] = ()
    person_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class _Span:
    start: datetime
    end: datetime


def _instant(day: date, clock: str | time, zone: tzinfo) -> datetime:
    # Local wall-clock time as a UTC instant (``24:00`` is the next midnight).
    if isinstance(clock, str):
        if clock == "24:00":
            day, clock = day + timedelta(days=1), time.min
        else:
            clock = time.fromisoformat(clock)
    return datetime.combine(day, clock, tzinfo=zone).astimezone(UTC)


def _spans_on(hours: OpeningHours, window: DayWindow) -> list[_Span]:
    # Opening intervals of the day, sorted; empty when closed that day.
    if window.day in hours.closed_dates:
        return []
    ranges = hours.weekly.get(_WEEKDAYS[window.day.weekday()], [])
    return sorted(
        (
            _Span(
                _instant(window.day, r.open, window.timezone),
                _instant(window.day, r.close, window.timezone),
            )
            for r in ranges
        ),
        key=lambda s: s.start,
    )


def _spans_for(place: PlaceRead, window: DayWindow) -> list[_Span]:
    # Intervals a place can be visited in; unknown hours mean the whole day.
    hours = place.hours.opening_hours
    if hours is None:
        return [_Span(_day_start(window), _day_end(window))]
    return _spans_on(hours, window)


def _day_start(window: DayWindow) -> datetime:
    return _instant(window.day, window.start, window.timezone)


def _day_end(window: DayWindow) -> datetime:
    return _instant(window.day, window.end, window.timezone)


def is_open_on(place: PlaceRead, day: date, timezone: tzinfo) -> bool:
    """Tell whether a place has any opening interval on a day.

    The solver uses it to leave closed places out of a day (E0).

    Args:
        place: The place.
        day: The local date.
        timezone: Zone of the city.

    Returns:
        False when the weekday is closed or the date is in ``closed_dates``;
        True when the hours are unknown.
    """
    hours = place.hours.opening_hours
    if hours is None:
        return True
    window = DayWindow(day, timezone, time.min, time.max)
    return bool(_spans_on(hours, window))


def _nap_spans(people: Sequence[Person], window: DayWindow) -> list[_Span]:
    # Merged nap intervals of everybody, clipped to the day window.
    start, end = _day_start(window), _day_end(window)
    naps = sorted(
        (
            _Span(
                (s := _instant(window.day, p.nap_start, window.timezone)),
                s + timedelta(minutes=p.nap_minutes),
            )
            for p in people
            if p.nap_start is not None and p.nap_minutes > 0
        ),
        key=lambda n: n.start,
    )
    merged: list[_Span] = []
    for nap in naps:
        if merged and nap.start <= merged[-1].end:
            merged[-1] = _Span(merged[-1].start, max(merged[-1].end, nap.end))
        else:
            merged.append(nap)
    return [n for n in merged if n.end > start and n.start < end]


def _settle(start: datetime, length: timedelta, blocked: Sequence[_Span]) -> datetime:
    # Earliest start at or after ``start`` that does not overlap a break.
    moved = True
    while moved:
        moved = False
        for span in blocked:
            if start < span.end and start + length > span.start:
                start, moved = span.end, True
    return start


def _order_key(
    place: PlaceRead, spans: Sequence[_Span]
) -> tuple[datetime, datetime, str]:
    return (max(s.end for s in spans), min(s.start for s in spans), str(place.id))


def _fit(
    place: PlaceRead,
    spans: Sequence[_Span],
    arrival: datetime,
    day_end: datetime,
    blocked: Sequence[_Span],
) -> _Span | None:
    # Earliest visit of a place that fits its hours, the day and the breaks.
    length = timedelta(minutes=place.typical_visit_min)
    for span in spans:
        start = _settle(max(arrival, span.start), length, blocked)
        if start + length <= min(span.end, day_end):
            return _Span(start, start + length)
    return None


def _too_far(distance_km: float, people: Sequence[Person]) -> list[UUID]:
    return sorted(p.id for p in people if distance_km > DAILY_KM_FACTOR * p.daily_km)


@dataclass(slots=True)
class _Lunch:
    """Lunch still to be placed (``span`` is set once it is)."""

    earliest: datetime
    latest: datetime
    length: timedelta
    pending: bool = True
    span: _Span | None = None


def _take_lunch(
    lunch: _Lunch, cursor: datetime, day_end: datetime, blocked: list[_Span]
) -> datetime:
    """Place lunch at or after the cursor if it still fits.

    Args:
        lunch: Lunch state; marked as no longer pending.
        cursor: End of the previous visit.
        day_end: End of the day window.
        blocked: Fixed breaks; the lunch is appended when placed.

    Returns:
        The new cursor (end of lunch, or unchanged when it was dropped).
    """
    lunch.pending = False
    begin = _settle(max(cursor, lunch.earliest), lunch.length, blocked)
    if begin > lunch.latest or begin + lunch.length > day_end:
        return cursor
    lunch.span = _Span(begin, begin + lunch.length)
    blocked.append(lunch.span)
    blocked.sort(key=lambda s: s.start)
    return lunch.span.end


def _local(span: _Span, zone: tzinfo) -> tuple[datetime, datetime]:
    return span.start.astimezone(zone), span.end.astimezone(zone)


def schedule_day(
    places: Sequence[PlaceRead], window: DayWindow, people: Sequence[Person]
) -> DaySchedule | Infeasible:
    """Order the places of one day and give them times.

    Order: earliest closing time, then earliest opening time, then id. Before
    a place goes its own ``transfer_min`` (not before the first one); naps of
    the people are fixed breaks, lunch starts in its window (at the latest
    before the first visit that would end after it). A lunch that no longer
    fits is dropped; counting that is the linter's job.

    Args:
        places: The chosen places.
        window: Date, zone and time window of the day.
        people: The people whose limits and naps apply.

    Returns:
        The schedule, or ``Infeasible``: ``closed`` (places closed that day),
        ``no_fit`` (a place does not fit its hours, the window and the breaks)
        or ``distance`` (E0: ``L_d`` over ``1.5 * D_i`` for the listed people).
    """
    spans = {p.id: _spans_for(p, window) for p in places}
    closed = sorted(p.id for p in places if not spans[p.id])
    if closed:
        return Infeasible(InfeasibleCode.CLOSED, place_ids=tuple(closed))

    day_end = _day_end(window)
    blocked = _nap_spans(people, window)
    naps = list(blocked)
    meal = None
    if window.lunch is not None and window.lunch.minutes > 0:
        meal = _Lunch(
            _instant(window.day, window.lunch.earliest, window.timezone),
            _instant(window.day, window.lunch.latest, window.timezone),
            timedelta(minutes=window.lunch.minutes),
        )
    cursor = _day_start(window)
    visits: list[ScheduledVisit] = []
    for place in sorted(places, key=lambda p: _order_key(p, spans[p.id])):
        transfer = place.transfer_min if visits else 0
        arrival = timedelta(minutes=transfer)
        fit = _fit(place, spans[place.id], cursor + arrival, day_end, blocked)
        if (
            meal is not None
            and meal.pending
            and (cursor >= meal.earliest or fit is None or fit.end > meal.latest)
        ):
            cursor = _take_lunch(meal, cursor, day_end, blocked)
            fit = _fit(place, spans[place.id], cursor + arrival, day_end, blocked)
        if fit is None:
            return Infeasible(InfeasibleCode.NO_FIT, place_ids=(place.id,))
        visits.append(
            ScheduledVisit(
                place.id, *_local(fit, window.timezone), transfer, place.segment_km
            )
        )
        cursor = fit.end

    distance = sum(p.segment_km for p in places)
    over = _too_far(distance, people)
    if over:
        return Infeasible(InfeasibleCode.DISTANCE, person_ids=tuple(over))
    breaks: list[tuple[Literal["nap", "lunch"], _Span]] = [("nap", n) for n in naps]
    if meal is not None and meal.span is not None:
        breaks.append(("lunch", meal.span))
    return DaySchedule(
        visits=tuple(visits),
        breaks=tuple(
            ScheduledBreak(kind, *_local(span, window.timezone))
            for kind, span in sorted(breaks, key=lambda b: b[1].start)
        ),
        distance_km=distance,
        active_min=sum(v.transfer_min for v in visits)
        + sum(p.typical_visit_min for p in places),
    )
