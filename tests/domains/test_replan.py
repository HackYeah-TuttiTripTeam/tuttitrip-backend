"""Rain replanning of the rest of a day (backend#74; an extension outside v1.0)."""

import asyncio
import time
import uuid
from datetime import time as clock
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from tests.fixtures.city import key_of, places
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.replan import (
    DayState,
    ReplanResult,
    rain_factors,
    replan_rest_of_day,
)
from tuttitrip.planning.logic.solver import PlanResult, solve
from tuttitrip.planning.plans.schemas import ReplanStatus
from tuttitrip.planning.plans.services import replan_service
from tuttitrip.planning.schemas import PlanningInput
from tuttitrip.profiles.services import profile_service
from tuttitrip.trips.schemas import TripMembership, TripRole

CATALOG = places()
INDOOR = {p.id: bool(p.indoor) for p in CATALOG.values()}


def one_morning() -> PlanningInput:
    """One person, one day that ends at 12:30: room for one long stop, not two."""
    full = planning_input(reference(), lodging=False)
    trip = full.trip.model_copy(
        update={"days": full.trip.days[:1], "day_end": clock(12, 30)}
    )
    return full.model_copy(update={"trip": trip, "people": full.people[:1]})


def only(data: PlanningInput, *keys: str) -> PlanningInput:
    return data.model_copy(
        update={"places": tuple(p for p in data.places if key_of(p) in keys)}
    )


def state_of(plan: PlanResult) -> tuple[DayState, tuple[tuple[uuid.UUID, ...]]]:
    visits = plan.days[0].schedule.visits
    state = DayState(
        tuple(v.place_id for v in visits), {v.place_id: v.start for v in visits}
    )
    return state, (tuple(sorted((v.place_id for v in visits), key=str)),)


def replan_park_day(
    *, rain: bool = True, params: AlgorithmParams = DEFAULT_PARAMS
) -> tuple[PlanningInput, PlanResult, ReplanResult]:
    data = one_morning()
    plan = solve(only(data, "park_oliwski"))
    state, current = state_of(plan)
    as_of = plan.days[0].schedule.visits[0].start - timedelta(hours=1)
    result = replan_rest_of_day(
        data, current, day=0, state=state, as_of=as_of, rain=rain, params=params
    )
    return data, plan, result


def test_rain_factors_follow_the_indoor_flag() -> None:
    factors = rain_factors(list(CATALOG.values()), DEFAULT_PARAMS)
    museum = CATALOG["muzeum_miejskie"]
    park = CATALOG["park_oliwski"]
    assert factors[museum.id] == pytest.approx(1.0)
    assert factors[park.id] == pytest.approx(0.3)
    unknown = park.model_copy(update={"id": uuid.uuid4(), "indoor": None})
    assert rain_factors([unknown], DEFAULT_PARAMS)[unknown.id] == pytest.approx(0.65)


def test_the_park_gives_way_to_an_indoor_place_in_rain() -> None:
    _, plan, result = replan_park_day()
    park = CATALOG["park_oliwski"].id
    assert [v.place_id for v in plan.days[0].schedule.visits] == [park]
    assert park in result.removed
    assert result.added
    assert all(INDOOR[p] for p in result.added)
    now = {v.place_id for v in result.evaluation.schedules[0].visits}
    assert park not in now
    assert now == set(result.added)


def test_without_rain_the_park_stays() -> None:
    _, _, result = replan_park_day(rain=False)
    park = CATALOG["park_oliwski"].id
    assert park not in result.removed
    assert park in {v.place_id for v in result.evaluation.schedules[0].visits}


def test_the_same_plan_and_moment_give_the_same_answer() -> None:
    _, _, first = replan_park_day()
    _, _, second = replan_park_day()
    assert first.removed == second.removed
    assert first.added == second.added
    assert first.shifted == second.shifted
    assert first.j_replan == second.j_replan


def test_the_answer_comes_in_well_under_a_second_without_a_model() -> None:
    started = time.perf_counter()
    replan_park_day()
    assert time.perf_counter() - started < 1.0  # about 50 ms; the limit is generous


def test_a_visit_that_started_stays_and_nothing_new_is_in_the_past() -> None:
    data = one_morning()
    plan = solve(only(data, "park_oliwski", "bar_mleczny"))
    state, current = state_of(plan)
    first = plan.days[0].schedule.visits[0]
    as_of = first.start + timedelta(minutes=1)
    result = replan_rest_of_day(data, current, day=0, state=state, as_of=as_of)
    visits = result.evaluation.schedules[0].visits
    kept = next(v for v in visits if v.place_id == first.place_id)
    assert kept.start == first.start
    assert all(v.start >= as_of for v in visits if v.place_id != first.place_id)
    assert first.place_id not in result.removed


def test_the_replanned_day_obeys_the_hard_rules() -> None:
    data, _, result = replan_park_day()
    schedule = result.evaluation.schedules[0]
    walker = data.people[0]
    assert schedule.distance_km <= 1.5 * walker.daily_km
    assert all(v.end.time() <= data.trip.day_end for v in schedule.visits)
    assert result.evaluation.cost <= data.trip.budget_max


def test_dearer_changes_keep_the_plan_as_it_was() -> None:
    expensive = AlgorithmParams(replan_change_penalty=50.0)
    _, _, result = replan_park_day(params=expensive)
    assert result.removed == ()
    assert result.added == ()


def test_other_days_are_not_touched() -> None:
    data = planning_input(reference(), lodging=False)
    plan = solve(only(data, "park_oliwski", "muzeum_miejskie", "zoo"))
    current = tuple(
        tuple(sorted((v.place_id for v in d.schedule.visits), key=str))
        for d in plan.days
    )
    first_day = plan.days[0].schedule.visits
    state = DayState(
        tuple(v.place_id for v in first_day), {v.place_id: v.start for v in first_day}
    )
    as_of = first_day[0].start - timedelta(hours=1)
    result = replan_rest_of_day(data, current, day=0, state=state, as_of=as_of)
    for index in (1, 2):
        after = {v.place_id for v in result.evaluation.schedules[index].visits}
        assert set(current[index]) == after


# --- who has to agree -----------------------------------------------------------------

TRIP = uuid.UUID("00000000-0000-4000-8000-000000000000")
OWN = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
OTHER = uuid.UUID("00000000-0000-4000-8000-0000000000bb")


def membership(role: TripRole) -> TripMembership:
    return TripMembership(trip_id=TRIP, sub="auth0|x", role=role)


@pytest.mark.parametrize(
    ("role", "affected", "expected"),
    [
        (TripRole.HOST, [OWN, OTHER], ReplanStatus.ACTIVE),
        (TripRole.CO_HOST, [OTHER], ReplanStatus.ACTIVE),
        (TripRole.MEMBER, [OTHER], ReplanStatus.PENDING_HOST),
        (TripRole.MEMBER, [OWN, OTHER], ReplanStatus.PENDING_HOST),
        (TripRole.MEMBER, [OWN], ReplanStatus.ACTIVE),
        (TripRole.MEMBER, [], ReplanStatus.ACTIVE),
    ],
)
def test_a_members_change_that_touches_others_waits_for_the_host(
    monkeypatch: pytest.MonkeyPatch,
    role: TripRole,
    affected: list[uuid.UUID],
    expected: ReplanStatus,
) -> None:
    monkeypatch.setattr(
        profile_service, "find_account_profile", AsyncMock(return_value=OWN)
    )
    status = asyncio.run(
        replan_service.status_of(AsyncMock(), membership(role), affected)
    )
    assert status is expected
