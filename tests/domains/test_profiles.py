"""Profiles: age defaults, weight presets, and who may change what."""

import asyncio
import functools
import uuid
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import fields
from datetime import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.profiles.logic.age_defaults import DEFAULTS, age_group_for
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
    WeightPreset,
    WeightsUpdate,
)
from tuttitrip.profiles.services import profile_service
from tuttitrip.profiles.services.profile_service import (
    ProfileAccountError,
    ProfileForbiddenError,
)
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.trips.schemas import TripMembership, TripRole
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
        age_group=group.value,
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
        (0, AgeGroup.CHILD),
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


def test_every_group_has_defaults_with_floor_30() -> None:
    assert set(DEFAULTS) == set(AgeGroup)
    assert {d.floor for d in DEFAULTS.values()} == {30}


def _people() -> list[WeightSubject]:
    groups = [AgeGroup.CHILD] * 3 + [AgeGroup.ADULT] * 2
    return [WeightSubject(uuid.uuid4(), g) for g in groups]


def test_pod_dzieci_children_two_adults_one() -> None:
    people = _people()
    weights = preset_weights(WeightPreset.POD_DZIECI, people)
    assert [weights[p.id] for p in people] == [2, 2, 2, 1, 1]


def test_po_rowno_is_all_ones() -> None:
    people = _people()
    assert set(preset_weights(WeightPreset.PO_ROWNO, people).values()) == {1.0}


def test_dzien_babci_is_a_global_weight_two_for_the_chosen_person() -> None:
    people = _people()
    weights = preset_weights(WeightPreset.DZIEN_BABCI, people, people[3].id)
    assert weights[people[3].id] == 2
    assert sorted(weights.values()) == [1, 1, 1, 1, 2]


def test_dzien_babci_needs_a_chosen_person() -> None:
    with pytest.raises(FocusProfileRequiredError):
        preset_weights(WeightPreset.DZIEN_BABCI, _people())


def test_weight_spread_up_to_three_is_fine() -> None:
    validate_weights([1, 3, 2])
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
        WeightsUpdate(preset=WeightPreset.PO_ROWNO, weights=[])


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
    assert read.nap_start is None


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
async def test_age_change_to_another_group_follows_its_defaults_unless_given(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    kid = _profile(age=10)
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
    assert read.segment_km == adult.segment_km
    assert read.daily_km == 8
    assert read.nap_minutes == 0


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
        WeightsUpdate(preset=WeightPreset.POD_DZIECI),
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


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = create_app()
    authorize(app, AuthenticatedUser(sub=BOB))
    app.dependency_overrides[get_session] = lambda: None
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


@pytest.mark.parametrize("weight", ["NaN", "Infinity", 101])
def test_nan_inf_and_huge_weights_are_rejected(
    client: TestClient, weight: float | str
) -> None:
    body = f'{{"weights": [{{"profile_id": "{uuid.uuid4()}", "weight": {weight}}}]}}'
    response = client.put(
        path("set_weights", trip_id=TRIP),
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
