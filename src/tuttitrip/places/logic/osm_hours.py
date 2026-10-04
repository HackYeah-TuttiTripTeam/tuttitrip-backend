"""Parser for the subset of OSM ``opening_hours`` the city sheet uses.

Supported: ``24/7``, weekday lists and ranges (``Mo,We-Fr``, wrapping ranges
like ``We-Mo``), several intervals per day (``00:00-05:00,09:00-24:00``),
``off``, rules without weekdays (every day), later rules overriding earlier
ones, intervals past midnight (split at midnight) and ``PH`` (public
holidays, which the weekly format cannot express, so it is ignored).

Month or date selectors (``Mar-Oct``, ``Jan 01-Feb 22``) cannot be stored in
the weekly format: :func:`parse_osm_hours` returns ``None`` (unknown hours)
instead of guessing a season. Anything else it does not understand raises
``ValueError``.
"""

import re
from datetime import date, timedelta

from tuttitrip.places.schemas import OpeningHours, TimeRange, Weekday

_WEEKDAYS = (
    ("Mo", Weekday.MON),
    ("Tu", Weekday.TUE),
    ("We", Weekday.WED),
    ("Th", Weekday.THU),
    ("Fr", Weekday.FRI),
    ("Sa", Weekday.SAT),
    ("Su", Weekday.SUN),
)
_INDEX = {code: i for i, (code, _) in enumerate(_WEEKDAYS)}
_INTERVAL = r"\d{1,2}:\d{2}-\d{1,2}:\d{2}"
_TIMES = re.compile(rf"(?P<times>{_INTERVAL}(?:\s*,\s*{_INTERVAL})*|off)$")
_MONTH = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|week|easter)", re.IGNORECASE
)
_DAY_RANGE = re.compile(
    r"^(?P<a>Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?P<b>Mo|Tu|We|Th|Fr|Sa|Su))?$"
)
_ISO_DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})(?: do (\d{4}-\d{2}-\d{2}))?$")
_MAX_CLOSED_SPAN_DAYS = 366
_MINUTES_PER_HOUR = 60
_END_OF_DAY = 24 * _MINUTES_PER_HOUR


def _minutes(clock: str) -> int:
    hours, minutes = (int(part) for part in clock.split(":"))
    total = hours * _MINUTES_PER_HOUR + minutes
    if minutes >= _MINUTES_PER_HOUR or total > _END_OF_DAY:
        msg = f"invalid time {clock!r}"
        raise ValueError(msg)
    return total


def _clock(minutes: int) -> str:
    return f"{minutes // _MINUTES_PER_HOUR:02d}:{minutes % _MINUTES_PER_HOUR:02d}"


def _days(selector: str) -> list[int]:
    """Weekday indexes (0 = Monday) of a selector such as ``Mo,We-Fr,PH``.

    Args:
        selector: Comma separated weekdays, ranges and ``PH``.

    Returns:
        The indexes, in order of appearance.

    Raises:
        ValueError: On a token that is not a weekday, range or ``PH``.
    """
    days: list[int] = []
    for token in (part.strip() for part in selector.split(",")):
        if token in {"PH", "SH"}:
            continue
        match = _DAY_RANGE.match(token)
        if match is None:
            msg = f"unsupported weekday selector {token!r}"
            raise ValueError(msg)
        first = _INDEX[match["a"]]
        last = _INDEX[match["b"]] if match["b"] else first
        days.extend((first + step) % 7 for step in range((last - first) % 7 + 1))
    return days


def _intervals(times: str) -> list[tuple[int, int]]:
    if times == "off":
        return []
    result = []
    for part in times.split(","):
        opens, closes = (_minutes(clock.strip()) for clock in part.split("-"))
        if opens == closes:
            msg = f"empty interval {part.strip()!r}"
            raise ValueError(msg)
        result.append((opens, closes or _END_OF_DAY))  # "00:00" closes at midnight
    return result


def parse_osm_hours(value: str) -> OpeningHours | None:
    """Convert an OSM ``opening_hours`` string to the weekly format.

    Args:
        value: The OSM string, e.g. ``Tu-Su 10:00-17:00; Mo off``.

    Returns:
        The weekly hours, or None when the string has month or date selectors
        (the hours are then unknown, not guessed).

    Raises:
        ValueError: When the string is malformed or uses unsupported syntax.
    """
    text = value.strip()
    if text == "24/7":
        full = [TimeRange(open="00:00", close="24:00")]
        return OpeningHours(weekly={day: list(full) for _, day in _WEEKDAYS})
    if _MONTH.search(text):
        return None
    days: dict[int, list[tuple[int, int]]] = {}
    for rule in (part.strip() for part in text.split(";") if part.strip()):
        match = _TIMES.search(rule)
        if match is None:
            msg = f"cannot read rule {rule!r}"
            raise ValueError(msg)
        selector = rule[: match.start()].strip()
        indexes = _days(selector) if selector else list(range(7))
        rule_days: dict[int, list[tuple[int, int]]] = {i: [] for i in indexes}
        for opens, closes in _intervals(match["times"]):
            for index in indexes:
                if closes > opens:
                    rule_days[index].append((opens, closes))
                else:  # past midnight: split at midnight
                    rule_days[index].append((opens, _END_OF_DAY))
                    rule_days.setdefault((index + 1) % 7, []).append((0, closes))
        days.update(rule_days)
    weekly = {
        day: [TimeRange(open=_clock(a), close=_clock(b)) for a, b in sorted(spans)]
        for i, (_, day) in enumerate(_WEEKDAYS)
        if (spans := days.get(i))
    }
    return OpeningHours(weekly=weekly)


def parse_closed_dates(value: str) -> list[date]:
    """Read the exact closure dates out of the free-text ``dni_zamkniecia``.

    Only segments that are exactly one ISO date or ``A do B`` (an inclusive
    range) count; anything with other words (``otwarcie dopiero o 11:00``) is
    ignored, so a partial closure never becomes a full-day one.

    Args:
        value: The cell text, segments separated by ``;``.

    Returns:
        The dates, sorted, without duplicates.

    Raises:
        ValueError: When a range is reversed, impossible or longer than a year.
    """
    found: set[date] = set()
    for segment in (part.strip() for part in value.split(";")):
        match = _ISO_DAY.match(segment)
        if match is None:
            continue
        first = date.fromisoformat(match[1])
        last = date.fromisoformat(match[2]) if match[2] else first
        if not first <= last <= first + timedelta(days=_MAX_CLOSED_SPAN_DAYS):
            msg = f"invalid closure range {segment!r}"
            raise ValueError(msg)
        found.update(first + timedelta(days=n) for n in range((last - first).days + 1))
    return sorted(found)
