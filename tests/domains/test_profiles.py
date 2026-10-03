"""Profiles: age defaults, weight presets, and who may change what."""

import asyncio
import functools
import uuid
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import fields, replace
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.profiles.logic.age_defaults import (
    DEFAULTS,
    age_group_for,
    customized_fields,
)
from tuttitrip.profiles.logic.weight_presets import (
    FocusProfileRequiredError,
    WeightRatioError,
    WeightSubject,
    preset_weights,
    validate_weights,
)
from tuttitrip.profiles.models import Profile
from tuttitrip.profiles.schemas import (
    AgeGroup,
    ProfileCreate,
    ProfileUpdate,
    ProfileWeightPreset,
    WeightItem,
    WeightsUpdate,
)
from tuttitrip.profiles.services import profile_service
from tuttitrip.profiles.services.profile_service import (
    ProfileAccountError,
    ProfileComfortError,
    ProfileForbiddenError,
)
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.trips.schemas import TripCreate, TripMembership, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripRoleError

TRIP = uuid.uuid4()
ANA = "auth0|ana"
BOB = "auth0|bob"


def _membership(sub: str, role: TripRole) -> TripMembership:
    return TripMembership(trip_id=TRIP, sub=sub, role=role)


def _profile(
    name: str = "Ola", age: int = 35, user_sub: str | None = None, weight: float = 1.0
) -> Profile:
    group = age_group_for(age)
    return Profile(
        id=uuid.uuid4(),
        trip_id=TRIP,
        display_name=name,
        age=age,
        user_sub=user_sub,
        weight=weight,
        **{f.name: getattr(DEFAULTS[group], f.name) for f in fields(DEFAULTS[group])},
    )


def _persist(_session: object, profile: Profile) -> Profile:
    profile.id = uuid.uuid4()
    profile.weight = 1.0
    return profile


_echo = AsyncMock(side_effect=_persist)


def _sync[**P](test: Callable[P, Coroutine[None, None, None]]) -> Callable[P, None]:
    """Run an async test body to completion (the project has no async plugin)."""

    @functools.wraps(test)
    def run(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(test(*args, **kwargs))

    return run


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock()


# --- logic ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "group"),
    [
        (0, AgeGroup.TODDLER),
        (3, AgeGroup.TODDLER),
        (4, AgeGroup.CHILD),
        (12, AgeGroup.CHILD),
        (13, AgeGroup.TEEN),
        (17, AgeGroup.TEEN),
        (18, AgeGroup.ADULT),
        (64, AgeGroup.ADULT),
        (65, AgeGroup.SENIOR),
    ],
)
def test_age_group_from_age(age: int, group: AgeGroup) -> None:
    assert age_group_for(age) is group


def test_customized_fields_lists_only_differences_in_schema_order() -> None:
    child = DEFAULTS[AgeGroup.CHILD]
    assert customized_fields(child, AgeGroup.CHILD) == []
    mine = replace(child, floor=50, daily_km=7.0, nap_start=None)
    assert customized_fields(mine, AgeGroup.CHILD) == [
        "daily_km",
        "nap_start",
        "floor",
    ]
    assert "daily_km" not in customized_fields(
        replace(child, daily_km=child.daily_km + 1e-12), AgeGroup.CHILD
    )


def test_every_group_has_defaults_with_floor_30() -> None:
    assert set(DEFAULTS) == set(AgeGroup)
    assert {d.floor for d in DEFAULTS.values()} == {30}


def _people() -> list[WeightSubject]:
    groups = [AgeGroup.CHILD, AgeGroup.TODDLER, AgeGroup.CHILD] + [AgeGroup.ADULT] * 2
    return [WeightSubject(uuid.uuid4(), g) for g in groups]


def test_pod_dzieci_children_two_adults_one() -> None:
    people = _people()
    weights = preset_weights(ProfileWeightPreset.POD_DZIECI, people)
    assert [weights[p.id] for p in people] == [2, 2, 2, 1, 1]


def test_po_rowno_is_all_ones() -> None:
    people = _people()
    assert set(preset_weights(ProfileWeightPreset.PO_ROWNO, people).values()) == {1.0}


def test_dzien_babci_is_a_global_weight_two_for_the_chosen_person() -> None:
    people = _people()
    weights = preset_weights(ProfileWeightPreset.DZIEN_BABCI, people, people[3].id)
    assert weights[people[3].id] == 2
    assert sorted(weights.values()) == [1, 1, 1, 1, 2]


def test_dzien_babci_needs_a_chosen_person() -> None:
    with pytest.raises(FocusProfileRequiredError):
        preset_weights(ProfileWeightPreset.DZIEN_BABCI, _people())


def test_weight_spread_up_to_three_is_fine() -> None:
    validate_weights([1, 3, 2])
    validate_weights([0.3, 0.9])
    validate_weights([])


@pytest.mark.parametrize("weights", [[4, 1], [0, 1], [-1, 1]])
def test_weight_spread_over_three_or_non_positive_is_rejected(
    weights: list[float],
) -> None:
    with pytest.raises(WeightRatioError):
        validate_weights(weights)


def test_weights_need_exactly_one_of_preset_or_weights() -> None:
    with pytest.raises(ValueError, match="either preset or weights"):
        WeightsUpdate()
    with pytest.raises(ValueError, match="either preset or weights"):
        WeightsUpdate(
            preset=ProfileWeightPreset.PO_ROWNO,
            weights=[WeightItem(profile_id=uuid.uuid4(), weight=1)],
        )


# --- service -------------------------------------------------------------


@_sync
async def test_new_profile_takes_defaults_from_age(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    monkeypatch.setattr(profile_service.db, "insert_profile", _echo)
    read = await profile_service.create_profile(
        session,
        _membership(ANA, TripRole.HOST),
        ProfileCreate(display_name="Kasia", age=6),
    )
    child = DEFAULTS[AgeGroup.CHILD]
    assert read.age_group is AgeGroup.CHILD
    assert read.segment_km == child.segment_km
    assert read.daily_km == child.daily_km
    assert read.active_min == child.active_min
    assert (read.nap_start, read.nap_minutes) == (time(13, 0), 60)
    assert read.floor == 30
    assert read.weight == pytest.approx(1.0)


@_sync
async def test_explicit_fields_beat_age_defaults(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    monkeypatch.setattr(profile_service.db, "insert_profile", _echo)
    read = await profile_service.create_profile(
        session,
        _membership(ANA, TripRole.HOST),
        ProfileCreate(display_name="Kasia", age=6, daily_km=7.5, nap_start=None),
    )
    assert read.daily_km == pytest.approx(7.5)
    assert (read.nap_start, read.nap_minutes) == (None, 0)


@_sync
async def test_cannot_link_an_account_that_is_not_on_the_trip(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    error = trip_service.TripNotFoundError(str(TRIP))
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=error))
    with pytest.raises(ProfileAccountError):
        await profile_service.create_profile(
            session,
            _membership(ANA, TripRole.HOST),
            ProfileCreate(display_name="X", age=30, user_sub=BOB),
        )


@_sync
async def test_cannot_link_an_account_twice(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock())
    monkeypatch.setattr(
        profile_service.db, "user_has_profile", AsyncMock(return_value=True)
    )
    with pytest.raises(ProfileAccountError):
        await profile_service.create_profile(
            session,
            _membership(ANA, TripRole.HOST),
            ProfileCreate(display_name="X", age=30, user_sub=BOB),
        )


@_sync
async def test_member_edits_own_profile(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    mine = _profile(user_sub=BOB)
    monkeypatch.setattr(
        profile_service.db, "select_profile", AsyncMock(return_value=mine)
    )
    read = await profile_service.update_profile(
        session, _membership(BOB, TripRole.MEMBER), mine.id, ProfileUpdate(daily_km=5)
    )
    assert read.daily_km == 5


@_sync
async def test_member_cannot_edit_someone_elses_profile(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    other = _profile(user_sub=ANA)
    monkeypatch.setattr(
        profile_service.db, "select_profile", AsyncMock(return_value=other)
    )
    with pytest.raises(ProfileForbiddenError):
        await profile_service.update_profile(
            session,
            _membership(BOB, TripRole.MEMBER),
            other.id,
            ProfileUpdate(daily_km=5),
        )


@_sync
async def test_member_cannot_edit_an_unlinked_profile(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    unlinked = _profile()
    monkeypatch.setattr(
        profile_service.db, "select_profile", AsyncMock(return_value=unlinked)
    )
    with pytest.raises(ProfileForbiddenError):
        await profile_service.update_profile(
            session,
            _membership(BOB, TripRole.MEMBER),
            unlinked.id,
            ProfileUpdate(display_name="Nowa"),
        )


@_sync
async def test_member_cannot_link_an_account(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    mine = _profile(user_sub=BOB)
    monkeypatch.setattr(
        profile_service.db, "select_profile", AsyncMock(return_value=mine)
    )
    with pytest.raises(ProfileForbiddenError):
        await profile_service.update_profile(
            session,
            _membership(BOB, TripRole.MEMBER),
            mine.id,
            ProfileUpdate(user_sub=ANA),
        )


@_sync
async def test_co_host_edits_any_profile(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    other = _profile(user_sub=BOB)
    monkeypatch.setattr(
        profile_service.db, "select_profile", AsyncMock(return_value=other)
    )
    read = await profile_service.update_profile(
        session,
        _membership(ANA, TripRole.CO_HOST),
        other.id,
        ProfileUpdate(display_name="Zosia"),
    )
    assert read.display_name == "Zosia"


@_sync
async def test_age_change_to_another_group_follows_defaults_unless_corrected(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    kid = _profile(age=10)
    kid.floor = 50  # corrected by the host
    kid.segment_km = 0.8  # corrected too
    monkeypatch.setattr(
        profile_service.db, "select_profile", AsyncMock(return_value=kid)
    )
    read = await profile_service.update_profile(
        session,
        _membership(ANA, TripRole.HOST),
        kid.id,
        ProfileUpdate(age=40, daily_km=8),
    )
    adult = DEFAULTS[AgeGroup.ADULT]
    assert read.age_group is AgeGroup.ADULT
    assert read.floor == 50
    assert read.segment_km == pytest.approx(0.8)
    assert read.daily_km == 8
    assert read.active_min == adult.active_min
    assert (read.nap_start, read.nap_minutes) == (None, 0)


@_sync
async def test_contradicting_result_is_rejected(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    adult = _profile(age=40)
    monkeypatch.setattr(
        profile_service.db, "select_profile", AsyncMock(return_value=adult)
    )
    with pytest.raises(ProfileComfortError):
        await profile_service.update_profile(
            session,
            _membership(ANA, TripRole.HOST),
            adult.id,
            ProfileUpdate(nap_minutes=30),
        )


@_sync
async def test_racing_account_link_maps_to_409_but_other_errors_pass(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock())
    monkeypatch.setattr(
        profile_service.db, "user_has_profile", AsyncMock(return_value=False)
    )
    other = IntegrityError(
        "INSERT", {}, Exception('violates "ck_profiles_floor_range"')
    )
    monkeypatch.setattr(
        profile_service.db, "insert_profile", AsyncMock(side_effect=other)
    )
    with pytest.raises(IntegrityError):
        await profile_service.create_profile(
            session,
            _membership(ANA, TripRole.HOST),
            ProfileCreate(display_name="X", age=30, user_sub=BOB),
        )
    race = IntegrityError("INSERT", {}, Exception('violates "uq_profiles_trip_id"'))
    monkeypatch.setattr(
        profile_service.db, "insert_profile", AsyncMock(side_effect=race)
    )
    with pytest.raises(ProfileAccountError):
        await profile_service.create_profile(
            session,
            _membership(ANA, TripRole.HOST),
            ProfileCreate(display_name="X", age=30, user_sub=BOB),
        )


@_sync
async def test_host_profile_is_an_adult_linked_to_the_host(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    insert = AsyncMock(side_effect=_persist)
    monkeypatch.setattr(profile_service.db, "insert_profile", insert)
    await profile_service.create_host_profile(session, TRIP, ANA)
    assert insert.await_args is not None
    host = insert.await_args.args[1]
    assert (host.user_sub, host.trip_id, host.age_group) == (ANA, TRIP, AgeGroup.ADULT)
    assert host.segment_km == DEFAULTS[AgeGroup.ADULT].segment_km


@_sync
async def test_create_trip_creates_the_host_profile(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    trip = SimpleNamespace(id=TRIP)
    monkeypatch.setattr(trip_service.db, "insert_trip", AsyncMock(return_value=trip))
    monkeypatch.setattr(trip_service, "_read", lambda *_: "read")
    host = AsyncMock()
    monkeypatch.setattr(profile_service, "create_host_profile", host)
    await trip_service.create_trip(session, ANA, TripCreate(name="X", destination="Y"))
    host.assert_awaited_once_with(session, TRIP, ANA)
    session.commit.assert_awaited_once()


@_sync
async def test_preset_pod_dzieci_sets_weights(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    people = [_profile("D1", 6), _profile("D2", 8), _profile("A1", 40)]
    monkeypatch.setattr(
        profile_service.db, "select_profiles_by_trip", AsyncMock(return_value=people)
    )
    result = await profile_service.set_weights(
        session,
        _membership(ANA, TripRole.CO_HOST),
        WeightsUpdate(preset=ProfileWeightPreset.POD_DZIECI),
    )
    assert {p.display_name: p.weight for p in result} == {"D1": 2, "D2": 2, "A1": 1}


@_sync
async def test_manual_weights_are_merged_and_checked_as_a_whole(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    one, two = _profile("A", 40), _profile("B", 40)
    monkeypatch.setattr(
        profile_service.db,
        "select_profiles_by_trip",
        AsyncMock(return_value=[one, two]),
    )
    too_much = WeightsUpdate.model_validate(
        {"weights": [{"profile_id": str(one.id), "weight": 4}]}
    )
    with pytest.raises(WeightRatioError):
        await profile_service.set_weights(
            session, _membership(ANA, TripRole.HOST), too_much
        )
    assert one.weight == 1


# --- HTTP ----------------------------------------------------------------


def _mock_session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = create_app()
    authorize(app, AuthenticatedUser(sub=BOB))
    app.dependency_overrides[get_session] = _mock_session
    monkeypatch.setattr(
        trip_service,
        "get_membership",
        AsyncMock(return_value=_membership(BOB, TripRole.CO_HOST)),
    )
    with TestClient(app) as test_client:
        yield test_client


def test_weights_4_and_1_give_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    people = [_profile("A", 40), _profile("B", 40)]
    monkeypatch.setattr(
        profile_service.db, "select_profiles_by_trip", AsyncMock(return_value=people)
    )
    body = {
        "weights": [
            {"profile_id": str(people[0].id), "weight": 4},
            {"profile_id": str(people[1].id), "weight": 1},
        ]
    }
    response = client.put(path("set_weights", trip_id=TRIP), json=body)
    assert response.status_code == 422


def test_dzien_babci_without_a_chosen_person_gives_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        profile_service.db, "select_profiles_by_trip", AsyncMock(return_value=[])
    )
    response = client.put(
        path("set_weights", trip_id=TRIP), json={"preset": "dzien_babci"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("method", "route", "body"),
    [
        ("post", "create_profile", {"display_name": "K", "age": 6}),
        ("put", "set_weights", {"preset": "po_rowno"}),
        ("delete", "delete_profile", None),
    ],
)
def test_member_gets_403_on_co_host_routes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    route: str,
    body: dict[str, object] | None,
) -> None:
    error = TripRoleError("Trip role 'co_host' required (you are 'member')")
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=error))
    params = {"trip_id": TRIP}
    if route == "delete_profile":
        params["profile_id"] = uuid.uuid4()
    url = path(route, **params)
    assert client.request(method, url, json=body).status_code == 403


@pytest.mark.parametrize("weight", ["NaN", "Infinity", -1])
def test_nan_inf_and_negative_weights_give_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, weight: float | str
) -> None:
    people = [_profile("A", 40)]
    monkeypatch.setattr(
        profile_service.db, "select_profiles_by_trip", AsyncMock(return_value=people)
    )
    body = f'{{"weights": [{{"profile_id": "{people[0].id}", "weight": {weight}}}]}}'
    response = client.put(
        path("set_weights", trip_id=TRIP),
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def _w(*ids: uuid.UUID) -> dict[str, object]:
    return {"weights": [{"profile_id": str(i), "weight": 1} for i in ids]}


def test_empty_weights_and_duplicate_profiles_give_422(client: TestClient) -> None:
    pid = uuid.uuid4()
    url = path("set_weights", trip_id=TRIP)
    assert client.put(url, json={"weights": []}).status_code == 422
    assert client.put(url, json=_w(pid, pid)).status_code == 422


def test_comfort_contradictions_are_rejected_by_the_schema() -> None:
    with pytest.raises(ValueError, match="segment_km"):
        ProfileCreate(display_name="X", age=30, segment_km=5, daily_km=2)
    with pytest.raises(ValueError, match="nap_start"):
        ProfileCreate(display_name="X", age=30, nap_minutes=30, nap_start=None)
    with pytest.raises(ValueError, match="nap_start"):
        ProfileUpdate(nap_minutes=0, nap_start=time(13, 0))


def test_customized_fields_over_http(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    kid = _profile(age=6)
    monkeypatch.setattr(
        profile_service.db, "select_profile", AsyncMock(return_value=kid)
    )
    monkeypatch.setattr(
        profile_service.db, "select_profiles_by_trip", AsyncMock(return_value=[kid])
    )
    url = path("list_profiles", trip_id=TRIP)
    assert client.get(url).json()[0]["customized_fields"] == []
    url = path("update_profile", trip_id=TRIP, profile_id=kid.id)
    body = client.patch(url, json={"daily_km": 7.5, "customized_fields": ["floor"]})
    assert body.json()["customized_fields"] == ["daily_km"]
    body = client.patch(url, json={"age": 30})
    assert body.json()["age_group"] == "adult"
    assert body.json()["customized_fields"] == ["daily_km"]


def test_delete_returns_204_404_and_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    person = _profile("A", 40)
    monkeypatch.setattr(
        profile_service.db, "select_profile", AsyncMock(return_value=person)
    )
    monkeypatch.setattr(profile_service.db, "delete_profile", AsyncMock())
    url = path("delete_profile", trip_id=TRIP, profile_id=person.id)
    assert client.delete(url).status_code == 204
    monkeypatch.setattr(
        profile_service.db, "select_profile", AsyncMock(return_value=None)
    )
    assert client.delete(url).status_code == 404
    error = TripRoleError("Trip role 'co_host' required (you are 'member')")
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=error))
    assert client.delete(url).status_code == 403
