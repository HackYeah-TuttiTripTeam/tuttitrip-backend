"""E0 (hard constraints on places): pure unit tests."""

from datetime import date, time
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tests.fixtures.city import key_of, place_id, places
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.places.schemas import (
    OpeningHours,
    PlaceRead,
    TimeRange,
    Weekday,
)
from tuttitrip.planning.logic.hard_constraints import (
    Candidates,
    RejectionCode,
    filter_places,
)
from tuttitrip.planning.schemas import PlanningInput, PlanningPerson

CATALOG = places()
MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)


def with_(p: PlanningPerson, **changes: object) -> PlanningPerson:
    return p.model_copy(update=changes)


def replace_place(
    data: PlanningInput, place: PlaceRead, **changes: object
) -> PlanningInput:
    changed = place.model_copy(update=changes)
    return data.model_copy(
        update={
            "places": tuple(changed if p.id == place.id else p for p in data.places)
        }
    )


def on_days(data: PlanningInput, *days: date, **trip: object) -> PlanningInput:
    new = data.trip.model_copy(update={"days": days, **trip})
    return data.model_copy(update={"trip": new})


def reasons(result: Candidates, key: str) -> set[RejectionCode]:
    return {r.code for r in result.reasons(place_id(key))}


@pytest.fixture
def data() -> PlanningInput:
    return planning_input(reference())


@pytest.fixture
def museum() -> PlaceRead:
    return CATALOG["muzeum_miejskie"]


def test_veto_rejects_the_place(data: PlanningInput) -> None:
    result = filter_places(data)
    assert RejectionCode.VETO in reasons(result, "restauracja_morska")
    assert place_id("restauracja_morska") not in {p.id for p in result.accepted}


def test_veto_names_the_person(data: PlanningInput) -> None:
    result = filter_places(data)
    rejection = next(
        r
        for r in result.reasons(place_id("restauracja_morska"))
        if r.code is RejectionCode.VETO
    )
    assert rejection.person_id == data.people[3].id


def test_stairs_reject_at_the_limit(data: PlanningInput, museum: PlaceRead) -> None:
    tower = replace_place(data, museum, stairs=1.0)
    sensitive = with_(data.people[0], stairs_sensitivity=0.9)
    result = filter_places(tower.model_copy(update={"people": (sensitive,)}))
    assert RejectionCode.STAIRS in reasons(result, "muzeum_miejskie")
    ok = with_(sensitive, stairs_sensitivity=0.89)
    result = filter_places(tower.model_copy(update={"people": (ok,)}))
    assert RejectionCode.STAIRS not in reasons(result, "muzeum_miejskie")


def test_segment_over_one_and_a_half_times_s_rejects(
    data: PlanningInput, museum: PlaceRead
) -> None:
    walker = with_(data.people[0], segment_km=1.0, stairs_sensitivity=0.0)
    far = replace_place(data, museum, segment_km=1.6)
    result = filter_places(far.model_copy(update={"people": (walker,)}))
    assert RejectionCode.SEGMENT in reasons(result, "muzeum_miejskie")
    edge = replace_place(data, museum, segment_km=1.5)
    result = filter_places(edge.model_copy(update={"people": (walker,)}))
    assert RejectionCode.SEGMENT not in reasons(result, "muzeum_miejskie")


def test_second_person_rejects_by_segment(
    data: PlanningInput, museum: PlaceRead
) -> None:
    # Ty (s = 3 km) is fine with 1.6 km, Kasia (s = 1 km) is not.
    far = replace_place(data, museum, segment_km=1.6, stairs=0.0)
    result = filter_places(far)
    found = [r for r in result.reasons(museum.id) if r.code is RejectionCode.SEGMENT]
    assert [r.person_id for r in found] == [data.people[1].id]


def test_second_person_rejects_by_stairs(
    data: PlanningInput, museum: PlaceRead
) -> None:
    # Only Babcia (sensitivity 0.9) is at the limit with stairs = 1.
    tower = replace_place(data, museum, stairs=1.0, segment_km=0.1)
    people = (data.people[0], data.people[3])
    result = filter_places(tower.model_copy(update={"people": people}))
    found = [r for r in result.reasons(museum.id) if r.code is RejectionCode.STAIRS]
    assert [r.person_id for r in found] == [data.people[3].id]


def test_closed_on_every_day_of_the_trip(data: PlanningInput) -> None:
    result = filter_places(on_days(data, MONDAY))
    assert reasons(result, "muzeum_miejskie") == {RejectionCode.CLOSED}


def test_open_on_one_day_is_enough(data: PlanningInput) -> None:
    result = filter_places(on_days(data, MONDAY, TUESDAY))
    assert not reasons(result, "muzeum_miejskie")


def test_closed_date_closes_the_place_that_day(
    data: PlanningInput, museum: PlaceRead
) -> None:
    hours = OpeningHours(
        weekly={d: [TimeRange(open="09:00", close="18:00")] for d in Weekday},
        closed_dates=[TUESDAY],
    )
    dated = replace_place(
        data, museum, hours=museum.hours.model_copy(update={"opening_hours": hours})
    )
    closed = filter_places(on_days(dated, TUESDAY))
    assert reasons(closed, "muzeum_miejskie") == {RejectionCode.CLOSED}
    open_ = filter_places(on_days(dated, TUESDAY, date(2026, 10, 7)))
    assert not reasons(open_, "muzeum_miejskie")


def test_does_not_fit_the_day_window(data: PlanningInput) -> None:
    short = data.trip.model_copy(
        update={"day_end": data.trip.day_start.replace(hour=10)}
    )
    result = filter_places(data.model_copy(update={"trip": short}))
    assert RejectionCode.NO_FIT in reasons(result, "hevelianum")


def test_no_fit_wins_over_closed(data: PlanningInput) -> None:
    # Monday the museum is closed, Tuesday it opens at 10:00 but the day ends 09:30.
    narrow = on_days(data, MONDAY, TUESDAY, day_end=time(9, 30))
    result = filter_places(narrow)
    assert reasons(result, "muzeum_miejskie") == {RejectionCode.NO_FIT}


def test_must_blocked_reports_rejected_unknown_and_lodging_ids(
    data: PlanningInput,
) -> None:
    sea = place_id("restauracja_morska")
    museum_id = place_id("muzeum_miejskie")
    hotel = place_id("hotel_centrum")
    unknown = uuid4()
    must = frozenset({sea, museum_id, hotel, unknown})
    result = filter_places(data.model_copy(update={"must": must}))
    assert set(result.must_blocked) == {sea, hotel, unknown}
    assert museum_id not in result.must_blocked


def test_accepted_places_have_no_reasons_and_lodging_is_not_a_candidate(
    data: PlanningInput,
) -> None:
    result = filter_places(data)
    accepted = {p.id for p in result.accepted}
    assert accepted
    assert not accepted & {r.place_id for r in result.rejections}
    assert all(p.category.value != "lodging" for p in result.accepted)
    assert {key_of(p) for p in result.accepted} <= set(CATALOG)


def test_reference_family_tower_is_rejected_for_grandma(data: PlanningInput) -> None:
    result = filter_places(data)
    assert RejectionCode.STAIRS in reasons(result, "wieza_widokowa")


def test_result_is_deterministic(data: PlanningInput) -> None:
    assert filter_places(data) == filter_places(data)


# --- input validation ------------------------------------------------------------


def test_trip_rejects_unknown_timezone_and_inverted_day(data: PlanningInput) -> None:
    trip = data.trip.model_dump()
    with pytest.raises(ValidationError, match="time zone"):
        type(data.trip).model_validate({**trip, "timezone": "Mars/Base"})
    with pytest.raises(ValidationError, match="day_start"):
        type(data.trip).model_validate({**trip, "day_end": trip["day_start"]})


def test_weights_may_differ_at_most_three_times(data: PlanningInput) -> None:
    raw = data.model_dump()
    raw["people"][0]["weight"] = 1.0
    raw["people"][1]["weight"] = 3.0
    PlanningInput.model_validate(raw)
    raw["people"][1]["weight"] = 3.5
    with pytest.raises(ValidationError, match="Weights"):
        PlanningInput.model_validate(raw)
