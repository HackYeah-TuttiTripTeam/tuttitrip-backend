"""A pasted plan, read by the worker, as a lint plan (pure).

The parse gives claims of the pasted text: times, amounts per person and the
catalog match. Where the text is silent the code does not invent a day: an item
without a day goes to the first day, one without a start time starts when the
previous one ended (or at the start of the day). An amount counts for the whole
group (per person times the people).
"""

from collections.abc import Collection, Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from tuttitrip.planning.linter.schemas import LintDay, LintItem, LintPlan
from tuttitrip.planning.trip_linter.schemas import PasteItemRead
from tuttitrip.shared.jobs.contracts import (
    ParsedPlanItem,
    ParsePastedPlanOutput,
    PlaceMatch,
)

DEFAULT_VISIT = timedelta(hours=1)
MINOR_UNITS = Decimal(100)


def _uuid(value: str | None, known: Collection[UUID]) -> UUID | None:
    try:
        found = None if value is None else UUID(value)
    except ValueError:
        return None
    return found if found in known else None


def _read(
    item: ParsedPlanItem,
    match: PlaceMatch | None,
    pick: UUID | None,
    known: Collection[UUID],
) -> PasteItemRead:
    worker_pick = _uuid(match.place_id, known) if match else None
    status = match.status if match else "unrecognized"
    if pick is not None:
        place_id, status = pick, "matched"
    else:
        place_id = worker_pick if status == "matched" else None
    return PasteItemRead(
        index=item.index,
        day=item.day,
        place_name=item.place_name,
        quote=item.quote,
        status=status,
        place_id=place_id,
        suggested_place_id=worker_pick if status == "needs_confirmation" else None,
        chosen_by_host=pick is not None,
        candidates=match.candidates if match else [],
    )


def _clock(value: str | None) -> time | None:
    return None if value is None else time.fromisoformat(value)


def paste_to_lint(  # ruff: ignore[too-many-arguments] the parse and the trip facts
    parsed: ParsePastedPlanOutput,
    picks: Mapping[int, UUID],
    *,
    first_day: date,
    day_start: time,
    group_size: int,
    known_places: Collection[UUID],
) -> tuple[LintPlan, list[PasteItemRead]]:
    """Turn a parse into a lint plan, and list how each item was matched.

    Args:
        parsed: The worker's output.
        picks: Place chosen by the host, by item index.
        first_day: First day of the trip (the pasted ``day`` 1).
        day_start: Start of the trip day, for an item without a time.
        group_size: People who pay every amount.
        known_places: Catalog places of the trip's city.

    Returns:
        The plan and the items with the place each was checked as.
    """
    matches = {m.item_index: m for m in parsed.matches}
    read = [
        _read(item, matches.get(item.index), picks.get(item.index), known_places)
        for item in parsed.items
    ]
    by_day: dict[int, list[LintItem]] = {}
    ends: dict[int, datetime] = {}
    for item, checked in zip(parsed.items, read, strict=True):
        day = item.day or 1
        date_ = first_day + timedelta(days=day - 1)
        start = _clock(item.start_time)
        if start is None:
            start = (ends.get(day) or datetime.combine(date_, day_start)).time()
        end = _clock(item.end_time)
        if end is not None and end <= start:
            end = None  # a contradictory end: the typical visit decides
        stop_end = datetime.combine(date_, end) if end else None
        ends[day] = stop_end or datetime.combine(date_, start) + DEFAULT_VISIT
        amount = Decimal(item.amount_minor or 0) / MINOR_UNITS * group_size
        by_day.setdefault(day, []).append(
            LintItem(
                name=item.place_name,
                place_id=checked.place_id,
                start=start,
                end=end,
                cost=amount,
            )
        )
    days = [
        LintDay(day=first_day + timedelta(days=n - 1), items=items)
        for n, items in sorted(by_day.items())
    ]
    return LintPlan(days=days), read
