"""Schedule of one day (E0, E2 inputs, spec section 9): pure unit tests."""

from datetime import UTC, date, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from tuttitrip.places.schemas import (
    OpeningHours,
    PlaceCategory,
    PlaceHours,
    PlaceRead,
    PlaceSource,
    TimeRange,
    Weekday,
)
from tuttitrip.planning.logic.schedule import (
    DaySchedule,
    DayWindow,
    Infeasible,
    InfeasibleCode,
    Lunch,
    Person,
    is_open_on,
    schedule_day,
)

BERLIN = ZoneInfo("Europe/Berlin")
WARSAW = ZoneInfo("Europe/Warsaw")
MONDAY = date(2026, 10, 5)
EVERY_DAY = list(Weekday)


def place(  # ruff: ignore[too-many-arguments] test factory
    n: int,
    *,
    open_: str | None = "09:00",
    close: str = "18:00",
    days: list[Weekday] | None = None,
    closed_dates: list[date] | None = None,
    visit: int = 60,
    transfer: int = 10,
    km: float = 0.5,
) -> PlaceRead:
    hours = None
    if open_ is not None:
        hours = OpeningHours(
            weekly={d: [TimeRange(open=open_, close=close)] for d in days or EVERY_DAY},
            closed_dates=closed_dates or [],
        )
    return PlaceRead(
        id=UUID(int=n),
        city_slug="krakow",
        name=f"p{n}",
        category=PlaceCategory.ATTRACTION,
        tags=[],
        lat=0,
        lon=0,
        osm_type=None,
        osm_id=None,
        google_place_id=None,
        hours=PlaceHours(
            opening_hours=hours, source_url=None, verified=False, checked_at=None
        ),
        prices=[],
        typical_visit_min=visit,
        segment_km=km,
        transfer_min=transfer,
        queue_min=0,
        stairs=0,
        wheelchair=None,
        indoor=None,
        iconic=False,
        cuisine=None,
        diet_tags=[],
        amenities=[],
        source_key=None,
        source=PlaceSource.SHEET,
    )


def window(
    day: date = MONDAY,
    zone: ZoneInfo = WARSAW,
    start: str = "09:00",
    end: str = "19:00",
    lunch: Lunch | None = None,
) -> DayWindow:
    return DayWindow(
        day, zone, time.fromisoformat(start), time.fromisoformat(end), lunch
    )


def adult(km: float = 12.0) -> Person:
    return Person(id=uuid4(), daily_km=km)


def ok(result: DaySchedule | Infeasible) -> DaySchedule:
    assert isinstance(result, DaySchedule), result
    return result


def test_closed_on_monday_is_infeasible_with_code_closed() -> None:
    museum = place(1, days=[d for d in Weekday if d is not Weekday.MON])
    result = schedule_day([museum], window(), [adult()])
    assert result == Infeasible(InfeasibleCode.CLOSED, place_ids=(museum.id,))
    assert not is_open_on(museum, MONDAY, WARSAW)
    assert is_open_on(museum, MONDAY + timedelta(days=1), WARSAW)


def test_closed_date_blocks_an_otherwise_open_weekday() -> None:
    zoo = place(1, closed_dates=[MONDAY])
    assert isinstance(schedule_day([zoo], window(), [adult()]), Infeasible)


def test_order_follows_earliest_closing_then_opening_then_id() -> None:
    late = place(1, close="18:00")
    early = place(2, close="12:00")
    mid = place(3, close="15:00")
    twin_a, twin_b = place(5, close="16:00"), place(4, close="16:00")
    day = ok(schedule_day([late, early, mid, twin_a, twin_b], window(), [adult()]))
    assert [v.place_id.int for v in day.visits] == [2, 3, 4, 5, 1]


def test_times_use_visit_and_transfer_but_not_before_opening() -> None:
    first = place(1, open_="10:00", close="12:00", visit=60)
    second = place(2, open_="09:00", close="18:00", visit=30, transfer=15)
    day = ok(schedule_day([first, second], window(), [adult()]))
    a, b = day.visits
    assert (a.start.hour, a.start.minute) == (10, 0)  # waits for opening
    assert (b.start.hour, b.start.minute) == (11, 15)  # 11:00 + transfer 15
    assert a.transfer_min == 0
    assert day.active_min == 60 + 30 + 15


def test_distance_and_active_time_are_reported() -> None:
    day = ok(
        schedule_day([place(1, km=1.5), place(2, km=2.0)], window(), [adult(km=10)])
    )
    assert day.distance_km == pytest.approx(3.5)


def test_e0_daily_distance_limit_is_one_and_a_half_times_d() -> None:
    child = Person(id=uuid4(), daily_km=4.0)
    heavy = [place(1, km=3.5), place(2, km=3.5)]  # 7 km > 6
    result = schedule_day(heavy, window(), [child, adult()])
    assert result == Infeasible(InfeasibleCode.DISTANCE, person_ids=(child.id,))
    light = [place(1, km=2.5), place(2, km=2.5)]  # 5 km <= 6
    assert isinstance(schedule_day(light, window(), [child]), DaySchedule)


def test_place_that_does_not_fit_the_day_is_no_fit() -> None:
    long = place(1, visit=300)
    result = schedule_day([long], window(end="12:00"), [adult()])
    assert result == Infeasible(InfeasibleCode.NO_FIT, place_ids=(long.id,))


def test_unknown_hours_mean_open_all_day() -> None:
    day = ok(schedule_day([place(1, open_=None)], window(), [adult()]))
    assert day.visits[0].start.hour == 9


def test_local_time_in_berlin_and_the_dst_change() -> None:
    # 29 March 2026: Berlin jumps from 02:00 to 03:00. A 60 min visit starting
    # at 01:30 ends at 03:30 local, because only 30 min precede the gap.
    dst_day = date(2026, 3, 29)
    spot = place(1, open_="00:00", close="24:00", visit=60)
    day = ok(
        schedule_day(
            [spot], window(dst_day, BERLIN, start="01:30", end="06:00"), [adult()]
        )
    )
    visit = day.visits[0]
    assert visit.start.tzinfo == BERLIN
    assert (visit.start.hour, visit.start.minute) == (1, 30)
    assert (visit.end.hour, visit.end.minute) == (3, 30)
    assert visit.end.astimezone(UTC) - visit.start.astimezone(UTC) == timedelta(
        minutes=60
    )


def test_hours_are_local_not_utc() -> None:
    summer = ok(
        schedule_day([place(1, open_="10:00")], window(MONDAY, BERLIN), [adult()])
    )
    winter = ok(
        schedule_day(
            [place(1, open_="10:00")], window(date(2026, 12, 7), BERLIN), [adult()]
        )
    )
    assert summer.visits[0].start.hour == winter.visits[0].start.hour == 10


def test_nap_is_a_fixed_break_and_visits_avoid_it() -> None:
    toddler = Person(id=uuid4(), daily_km=2.0, nap_start=time(13, 0), nap_minutes=90)
    spots = [place(1, close="13:30", visit=60), place(2, close="18:00", visit=60)]
    day = ok(schedule_day(spots, window(start="11:00"), [toddler, adult()]))
    (nap,) = day.breaks
    assert nap.kind == "nap"
    assert (nap.start.hour, nap.end.hour, nap.end.minute) == (13, 14, 30)
    second = day.visits[1]
    assert second.start >= nap.end


def test_lunch_starts_in_its_window_before_a_visit_that_would_overrun_it() -> None:
    lunch = Lunch(time(12, 0), time(13, 0), 45)
    spots = [
        place(1, close="12:00", visit=60),
        place(2, close="18:00", visit=120, transfer=0),
    ]
    day = ok(schedule_day(spots, window(start="10:30", lunch=lunch), [adult()]))
    (meal,) = day.breaks
    assert meal.kind == "lunch"
    assert (meal.start.hour, meal.start.minute) == (12, 0)
    assert day.visits[1].start >= meal.end


def test_lunch_that_does_not_fit_is_dropped() -> None:
    lunch = Lunch(time(12, 0), time(13, 0), 45)
    day = ok(
        schedule_day(
            [place(1, visit=30)],
            window(start="09:00", end="10:00", lunch=lunch),
            [adult()],
        )
    )
    assert day.breaks == ()


def test_same_inputs_give_the_same_result() -> None:
    spots = [place(n, close=f"{12 + n}:00") for n in (3, 1, 2)]
    people = [adult()]
    assert schedule_day(spots, window(), people) == schedule_day(
        list(reversed(spots)), window(), people
    )


def test_lunch_after_the_last_visit_is_still_scheduled() -> None:
    lunch = Lunch(time(12, 0), time(13, 0), 45)
    day = ok(
        schedule_day(
            [place(1, visit=30)], window(start="09:00", lunch=lunch), [adult()]
        )
    )
    (meal,) = day.breaks
    assert (meal.kind, meal.start.hour) == ("lunch", 12)
