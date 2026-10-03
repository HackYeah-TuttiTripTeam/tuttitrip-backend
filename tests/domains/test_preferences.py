"""Preferences: importance pool, shared taxonomies, who may write, stairs link."""

import asyncio
import functools
import uuid
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.places.schemas import Cuisine, DietTag, PlaceTag
from tuttitrip.places.services import place_service
from tuttitrip.profiles import db as profile_db
from tuttitrip.profiles.logic.age_defaults import DEFAULTS, age_group_for
from tuttitrip.profiles.models import Profile
from tuttitrip.profiles.preferences import db
from tuttitrip.profiles.preferences.logic.access import stairs_sensitivity
from tuttitrip.profiles.preferences.logic.importance import (
    default_pool,
    renormalize,
)
from tuttitrip.profiles.preferences.models import ProfilePreferences
from tuttitrip.profiles.preferences.schemas import (
    ImportanceDomain,
    ImportancePool,
    MinTag,
    PreferencesWrite,
)
from tuttitrip.profiles.preferences.services import preference_service
from tuttitrip.profiles.preferences.services.preference_service import (
    ExamplePlaceNotFoundError,
)
from tuttitrip.profiles.schemas import AgeGroup
from tuttitrip.profiles.services.profile_service import ProfileForbiddenError
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripRoleError

TRIP = uuid.uuid4()
ANA = "auth0|ana"
BOB = "auth0|bob"
POOL = {"lodging": 2, "food": 2, "attractions": 3, "pace": 1, "cost": 2}


def _sync[**P](test: Callable[P, Coroutine[None, None, None]]) -> Callable[P, None]:
    @functools.wraps(test)
    def run(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(test(*args, **kwargs))

    return run


def _membership(sub: str, role: TripRole) -> TripMembership:
    return TripMembership(trip_id=TRIP, sub=sub, role=role)


def _profile(age: int = 35, user_sub: str | None = None) -> Profile:
    group = age_group_for(age)
    return Profile(
        id=uuid.uuid4(),
        trip_id=TRIP,
        display_name="Ola",
        age=age,
        user_sub=user_sub,
        weight=1.0,
        **{f.name: getattr(DEFAULTS[group], f.name) for f in fields(DEFAULTS[group])},
    )


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock()


def _store(monkeypatch: pytest.MonkeyPatch, profile: Profile) -> list[object]:
    """Fake the queries; returns the rows inserted."""
    inserted: list[object] = []

    def insert(_s: object, row: object) -> object:
        inserted.append(row)
        return row

    monkeypatch.setattr(profile_db, "select_profile", AsyncMock(return_value=profile))
    monkeypatch.setattr(db, "select_preferences", AsyncMock(return_value=None))
    monkeypatch.setattr(db, "insert_preferences", AsyncMock(side_effect=insert))
    return inserted


# --- logic and schemas ---------------------------------------------------


@pytest.mark.parametrize("group", list(AgeGroup))
def test_default_pool_of_every_group_adds_up_to_ten(group: AgeGroup) -> None:
    pool = default_pool(group)
    assert set(pool) == set(ImportanceDomain)
    assert sum(pool.values()) == 10
    ImportancePool.model_validate({d.value: p for d, p in pool.items()})


@pytest.mark.parametrize("body", [{"food": 9}, {"food": 5, "cost": 6}, {}])
def test_pool_that_does_not_add_up_to_ten_is_rejected(body: dict[str, int]) -> None:
    body = dict.fromkeys(POOL, 0) | body
    with pytest.raises(ValidationError, match="exactly 10"):
        ImportancePool.model_validate(body)


def test_pool_points_stay_within_zero_to_ten() -> None:
    with pytest.raises(ValidationError):
        ImportancePool.model_validate(POOL | {"pace": -1, "cost": 4})


def test_renormalize_drops_inactive_domains_like_a_trip_without_stays() -> None:
    pool = {ImportanceDomain(k): v for k, v in POOL.items()}
    active = frozenset(set(ImportanceDomain) - {ImportanceDomain.LODGING})
    result = renormalize(pool, active)
    assert ImportanceDomain.LODGING not in result
    assert sum(result.values()) == pytest.approx(1)
    assert result[ImportanceDomain.ATTRACTIONS] == pytest.approx(3 / 8)


def test_renormalize_splits_evenly_when_active_domains_have_no_points() -> None:
    pool = {ImportanceDomain(k): 0 for k in POOL} | {ImportanceDomain.LODGING: 10}
    active = frozenset({ImportanceDomain.FOOD, ImportanceDomain.COST})
    assert renormalize(pool, active) == {
        ImportanceDomain.FOOD: 0.5,
        ImportanceDomain.COST: 0.5,
    }
    assert renormalize(pool, frozenset()) == {}


def test_interests_use_the_places_taxonomy_and_stay_within_0_1() -> None:
    ok = PreferencesWrite.model_validate({"interests": {"history": 0.8, "kids": 0}})
    assert ok.interests == {PlaceTag.HISTORY: 0.8, PlaceTag.KIDS: 0}
    for bad in ({"opera": 0.5}, {"history": 1.2}, {"history": -0.1}):
        with pytest.raises(ValidationError):
            PreferencesWrite.model_validate({"interests": bad})


def test_diet_uses_the_catalog_codes_and_allergies_are_text() -> None:
    ok = PreferencesWrite.model_validate(
        {"diet": {"tags": ["vegan", "halal"], "allergies": ["orzechy"]}}
    )
    assert ok.diet.tags == [DietTag.VEGAN, DietTag.HALAL]
    for bad in ({"tags": ["paleo"]}, {"tags": ["vegan", "vegan"]}):
        with pytest.raises(ValidationError):
            PreferencesWrite.model_validate({"diet": bad})


def test_min_tag_must_come_from_its_domains_taxonomy() -> None:
    assert MinTag(domain="food", tag="indian").tag == Cuisine.INDIAN
    assert MinTag(domain="attractions", tag="museums").tag == PlaceTag.MUSEUMS
    with pytest.raises(ValidationError):
        MinTag(domain="food", tag="museums")
    with pytest.raises(ValidationError):
        MinTag(domain="attractions", tag="indian")


@pytest.mark.parametrize(
    ("blocked", "current", "expected"),
    [(True, 0.2, 1.0), (False, 1.0, 0.2), (False, 0.5, 0.5), (True, 1.0, 1.0)],
)
def test_stairs_sensitivity_follows_the_constraint(
    *, blocked: bool, current: float, expected: float
) -> None:
    assert stairs_sensitivity(
        blocked=blocked, current=current, age_default=0.2
    ) == pytest.approx(expected)


# --- service -------------------------------------------------------------


@_sync
async def test_unfilled_preferences_are_the_age_default(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    toddler = _profile(age=2)
    _store(monkeypatch, toddler)
    read = await preference_service.get_preferences(
        session, _membership(BOB, TripRole.MEMBER), toddler.id
    )
    assert read.filled is False
    assert read.importance_pool.model_dump() == {
        d.value: p for d, p in default_pool(AgeGroup.TODDLER).items()
    }


@_sync
async def test_host_saves_grandmas_stairs_and_the_list_returns_them(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    gran = _profile(age=70)
    inserted = _store(monkeypatch, gran)
    body = PreferencesWrite.model_validate(
        {"constraints": {"stairs": True}, "importance_pool": POOL}
    )
    saved = await preference_service.replace_preferences(
        session, _membership(ANA, TripRole.HOST), gran.id, body
    )
    assert saved.constraints.stairs is True
    assert gran.stairs_sensitivity == pytest.approx(1.0)
    row = inserted[0]
    assert isinstance(row, ProfilePreferences)
    assert row.updated_by_sub == ANA
    monkeypatch.setattr(
        profile_db, "select_profiles_by_trip", AsyncMock(return_value=[gran])
    )
    monkeypatch.setattr(db, "select_preferences_by_trip", AsyncMock(return_value=[row]))
    listed = await preference_service.list_preferences(
        session, _membership(BOB, TripRole.MEMBER)
    )
    assert [(p.profile_id, p.constraints.stairs, p.filled) for p in listed] == [
        (gran.id, True, True)
    ]


@_sync
async def test_clearing_stairs_gives_back_the_age_default(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    gran = _profile(age=70)
    gran.stairs_sensitivity = 1.0
    _store(monkeypatch, gran)
    await preference_service.replace_preferences(
        session, _membership(ANA, TripRole.HOST), gran.id, PreferencesWrite()
    )
    assert gran.stairs_sensitivity == pytest.approx(
        DEFAULTS[AgeGroup.SENIOR].stairs_sensitivity
    )


@_sync
async def test_second_save_updates_the_same_row(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    me = _profile(user_sub=BOB)
    _store(monkeypatch, me)
    row = SimpleNamespace(
        updated_by_sub="x", interests={}, updated_at=None, profile_id=me.id
    )
    monkeypatch.setattr(db, "select_preferences", AsyncMock(return_value=row))
    insert = AsyncMock()
    monkeypatch.setattr(db, "insert_preferences", AsyncMock(side_effect=insert))
    body = PreferencesWrite.model_validate({"interests": {"art": 1}})
    await preference_service.replace_preferences(
        session, _membership(BOB, TripRole.MEMBER), me.id, body
    )
    insert.assert_not_awaited()
    assert row.interests == {"art": 1}
    assert row.updated_by_sub == BOB


@_sync
async def test_member_cannot_write_someone_elses_preferences(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    other = _profile(user_sub=ANA)
    _store(monkeypatch, other)
    with pytest.raises(ProfileForbiddenError):
        await preference_service.replace_preferences(
            session,
            _membership(BOB, TripRole.MEMBER),
            other.id,
            PreferencesWrite(),
        )


@_sync
async def test_member_cannot_write_an_unlinked_profile_but_co_host_can(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    child = _profile(age=6)
    _store(monkeypatch, child)
    with pytest.raises(ProfileForbiddenError):
        await preference_service.replace_preferences(
            session, _membership(BOB, TripRole.MEMBER), child.id, PreferencesWrite()
        )
    saved = await preference_service.replace_preferences(
        session, _membership(BOB, TripRole.CO_HOST), child.id, PreferencesWrite()
    )
    assert saved.importance_pool.model_dump() == {
        d.value: p for d, p in default_pool(AgeGroup.CHILD).items()
    }


@_sync
async def test_example_place_must_be_in_the_catalog_when_it_has_an_id(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    me = _profile(user_sub=BOB)
    _store(monkeypatch, me)
    missing = place_service.PlaceNotFoundError("x")
    monkeypatch.setattr(place_service, "get_place", AsyncMock(side_effect=missing))
    known = {"name": "Muzeum", "place_id": str(uuid.uuid4()), "verdict": "like"}
    with pytest.raises(ExamplePlaceNotFoundError):
        await preference_service.replace_preferences(
            session,
            _membership(BOB, TripRole.MEMBER),
            me.id,
            PreferencesWrite.model_validate({"example_places": [known]}),
        )
    free_text = {"name": "Pizzeria u Zenka", "verdict": "dislike"}
    saved = await preference_service.replace_preferences(
        session,
        _membership(BOB, TripRole.MEMBER),
        me.id,
        PreferencesWrite.model_validate({"example_places": [free_text]}),
    )
    assert saved.example_places[0].place_id is None


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
        AsyncMock(return_value=_membership(BOB, TripRole.MEMBER)),
    )
    with TestClient(app) as test_client:
        yield test_client


def test_pool_summing_to_nine_gives_422_mentioning_ten(client: TestClient) -> None:
    url = path("put_preferences", trip_id=TRIP, profile_id=uuid.uuid4())
    response = client.put(url, json={"importance_pool": POOL | {"cost": 1}})
    assert response.status_code == 422
    assert "exactly 10" in response.text


def test_member_writing_another_person_gets_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _profile(user_sub=ANA)
    _store(monkeypatch, other)
    url = path("put_preferences", trip_id=TRIP, profile_id=other.id)
    assert client.put(url, json={}).status_code == 403


def test_unknown_profile_gives_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_db, "select_profile", AsyncMock(return_value=None))
    url = path("get_preferences", trip_id=TRIP, profile_id=uuid.uuid4())
    assert client.get(url).status_code == 404
    assert client.put(url, json={}).status_code == 404


def test_someone_outside_the_trip_gets_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gone = trip_service.TripNotFoundError(str(TRIP))
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=gone))
    assert client.get(path("list_preferences", trip_id=TRIP)).status_code == 404


def test_low_role_error_maps_to_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = TripRoleError("Trip role 'member' required")
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=error))
    assert client.get(path("list_preferences", trip_id=TRIP)).status_code == 403


def test_get_returns_defaults_over_http(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    me = _profile(user_sub=BOB)
    _store(monkeypatch, me)
    url = path("get_preferences", trip_id=TRIP, profile_id=me.id)
    body = client.get(url).json()
    assert body["filled"] is False
    assert sum(body["importance_pool"].values()) == 10
