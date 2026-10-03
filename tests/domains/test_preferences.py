"""Preferences: pool, taxonomies, ratings link, privacy, upsert, stairs rule."""

import asyncio
import functools
import uuid
from collections.abc import Callable, Collection, Coroutine, Iterator
from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.places.schemas import Cuisine, DietTag, PlaceTag
from tuttitrip.places.services import place_service
from tuttitrip.profiles.feedback.schemas import (
    RatingRead,
    RatingUpdate,
    RatingValue,
    ReasonCode,
)
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.profiles.logic.age_defaults import DEFAULTS, age_group_for
from tuttitrip.profiles.preferences import db
from tuttitrip.profiles.preferences.logic.access import effective_stairs_sensitivity
from tuttitrip.profiles.preferences.logic.importance import default_pool
from tuttitrip.profiles.preferences.schemas import (
    Constraints,
    ImportanceDomain,
    ImportancePool,
    MinTag,
    PreferencesWrite,
)
from tuttitrip.profiles.preferences.services import preference_service
from tuttitrip.profiles.preferences.services.preference_service import (
    ExamplePlaceNotFoundError,
)
from tuttitrip.profiles.schemas import AgeGroup, ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.profiles.services.profile_service import (
    ProfileForbiddenError,
    ProfileNotFoundError,
)
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


def test_interests_use_the_places_taxonomy_and_stay_within_0_1() -> None:
    ok = PreferencesWrite.model_validate({"interests": {"history": 0.8, "kids": 0}})
    assert ok.interests == {PlaceTag.HISTORY: 0.8, PlaceTag.KIDS: 0}
    for bad in ({"opera": 0.5}, {"history": 1.2}, {"history": -0.1}):
        with pytest.raises(ValidationError):
            PreferencesWrite.model_validate({"interests": bad})


def test_duplicate_min_tags_and_example_place_ids_are_rejected() -> None:
    tag = {"domain": "food", "tag": "indian"}
    with pytest.raises(ValidationError, match="only once"):
        PreferencesWrite.model_validate({"min_tags": [tag, tag]})
    pid = str(uuid.uuid4())
    place = {"name": "A", "place_id": pid, "verdict": "like"}
    with pytest.raises(ValidationError, match="place_id"):
        PreferencesWrite.model_validate({"example_places": [place, place]})


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
    ("stairs", "wheelchair", "expected"),
    [(True, False, 1.0), (False, True, 1.0), (False, False, 0.35)],
)
def test_effective_stairs_sensitivity_is_computed_not_stored(
    *, stairs: bool, wheelchair: bool, expected: float
) -> None:
    constraints = Constraints(stairs=stairs, wheelchair=wheelchair)
    assert effective_stairs_sensitivity(constraints, 0.35) == pytest.approx(expected)


# --- fakes ---------------------------------------------------------------


def _profile(age: int = 35, user_sub: str | None = None, **extra: float) -> ProfileRead:
    group = age_group_for(age)
    return ProfileRead.model_validate(
        {
            "id": uuid.uuid4(),
            "trip_id": TRIP,
            "display_name": "Ola",
            "age": age,
            "age_group": group,
            "user_sub": user_sub,
            "weight": 1.0,
            **asdict(DEFAULTS[group]),
            **extra,
        }
    )


def _row(profile: ProfileRead, **extra: object) -> SimpleNamespace:
    base = {
        "profile_id": profile.id,
        "interests": {},
        "importance_pool": POOL,
        "constraints": {},
        "diet": {},
        "example_places": [],
        "min_tags": [],
        "updated_by_sub": ANA,
        "updated_at": datetime(2026, 10, 3, tzinfo=UTC),
    }
    return SimpleNamespace(**(base | extra))


def _rating(profile: ProfileRead, place: uuid.UUID, value: RatingValue) -> RatingRead:
    return RatingRead(
        trip_id=TRIP,
        profile_id=profile.id,
        place_id=place,
        value=value,
        reason_code=ReasonCode.OTHER if value is RatingValue.DONT_WANT else None,
        updated_by_sub=ANA,
        updated_at=datetime(2026, 10, 3, tzinfo=UTC),
    )


class World:
    """Patched collaborators; records what the service wrote."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *people: ProfileRead) -> None:
        self.people = list(people)
        self.rows: list[SimpleNamespace] = []
        self.ratings: list[RatingRead] = []
        self.places: dict[uuid.UUID, SimpleNamespace] = {}
        self.upserts: list[tuple[uuid.UUID, dict[str, Any], str]] = []
        self.staged: list[tuple[uuid.UUID, uuid.UUID, Any]] = []

        def get_profile(_s: object, _m: object, profile_id: uuid.UUID) -> ProfileRead:
            for person in self.people:
                if person.id == profile_id:
                    return person
            raise ProfileNotFoundError(str(profile_id))

        def upsert(
            _s: object, profile_id: uuid.UUID, values: dict[str, Any], sub: str
        ) -> SimpleNamespace:
            self.upserts.append((profile_id, values, sub))
            row = _row(
                next(p for p in self.people if p.id == profile_id),
                **values,
                updated_by_sub=sub,
            )
            self.rows = [r for r in self.rows if r.profile_id != profile_id] + [row]
            return row

        def stage(
            _s: object,
            _m: object,
            profile_id: uuid.UUID,
            place_id: uuid.UUID,
            data: RatingUpdate,
        ) -> None:
            self.staged.append((profile_id, place_id, data))
            self.ratings.append(
                _rating(
                    next(p for p in self.people if p.id == profile_id),
                    place_id,
                    data.value,
                )
            )

        def get_places(
            _s: object, ids: Collection[uuid.UUID]
        ) -> dict[uuid.UUID, SimpleNamespace]:
            return {i: self.places[i] for i in ids if i in self.places}

        patch = monkeypatch.setattr
        patch(profile_service, "get_profile", AsyncMock(side_effect=get_profile))
        patch(
            profile_service,
            "list_profiles",
            AsyncMock(side_effect=lambda *_: self.people),
        )
        patch(db, "upsert_preferences", AsyncMock(side_effect=upsert))
        patch(
            db,
            "select_preferences_by_trip",
            AsyncMock(side_effect=lambda *_: self.rows),
        )
        patch(
            feedback_service,
            "list_ratings",
            AsyncMock(side_effect=lambda *_: self.ratings),
        )
        patch(feedback_service, "stage_rating", AsyncMock(side_effect=stage))
        patch(place_service, "get_places", AsyncMock(side_effect=get_places))

    def add_place(self, name: str = "Muzeum") -> uuid.UUID:
        place_id = uuid.uuid4()
        self.places[place_id] = SimpleNamespace(name=name)
        return place_id


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock()


# --- service -------------------------------------------------------------


@_sync
async def test_unfilled_preferences_are_the_age_default_and_nothing_is_stored(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    toddler = _profile(age=2, user_sub=BOB)
    world = World(monkeypatch, toddler)
    read = await preference_service.get_preferences(
        session, _membership(BOB, TripRole.MEMBER), toddler.id
    )
    assert read.filled is False
    assert read.importance_pool.model_dump() == {
        d.value: p for d, p in default_pool(AgeGroup.TODDLER).items()
    }
    assert world.upserts == []
    session.commit.assert_not_awaited()


@_sync
async def test_host_saves_grandmas_stairs_and_everyone_sees_the_effect(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    gran = _profile(age=70)
    world = World(monkeypatch, gran)
    body = PreferencesWrite.model_validate(
        {"constraints": {"stairs": True}, "importance_pool": POOL}
    )
    saved = await preference_service.replace_preferences(
        session, _membership(ANA, TripRole.HOST), gran.id, body
    )
    assert saved.constraints is not None
    assert saved.constraints.stairs is True
    assert saved.effective_stairs_sensitivity == pytest.approx(1.0)
    assert world.upserts[0][2] == ANA
    session.commit.assert_awaited_once()
    listed = await preference_service.list_preferences(
        session, _membership(ANA, TripRole.CO_HOST)
    )
    assert [(p.profile_id, p.filled) for p in listed] == [(gran.id, True)]
    assert listed[0].constraints is not None
    assert listed[0].constraints.stairs is True


@_sync
async def test_stairs_rule_does_not_write_the_profile_and_follows_age_changes(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    gran = _profile(age=70)
    world = World(monkeypatch, gran)
    host = _membership(ANA, TripRole.HOST)
    body = PreferencesWrite.model_validate({"constraints": {"stairs": True}})
    await preference_service.replace_preferences(session, host, gran.id, body)
    assert gran.stairs_sensitivity == pytest.approx(
        DEFAULTS[AgeGroup.SENIOR].stairs_sensitivity
    )
    # Cleared again: the senior default applies, not a leftover 1.0.
    cleared = await preference_service.replace_preferences(
        session, host, gran.id, PreferencesWrite()
    )
    assert cleared.effective_stairs_sensitivity == pytest.approx(
        DEFAULTS[AgeGroup.SENIOR].stairs_sensitivity
    )
    # The person gets younger (another group): the computed value follows.
    world.people[0] = _profile(age=20).model_copy(update={"id": gran.id})
    again = await preference_service.get_preferences(session, host, gran.id)
    assert again.effective_stairs_sensitivity == pytest.approx(
        DEFAULTS[AgeGroup.ADULT].stairs_sensitivity
    )


@_sync
async def test_health_data_is_hidden_from_plain_members_but_not_from_the_person(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    gran = _profile(age=70, user_sub=ANA)
    other = _profile(user_sub=BOB)
    world = World(monkeypatch, gran, other)
    world.rows = [
        _row(
            gran,
            constraints={"stairs": True, "disability_note": "kolano"},
            interests={"history": 1},
        )
    ]
    as_member = await preference_service.list_preferences(
        session, _membership(BOB, TripRole.MEMBER)
    )
    by_id = {p.profile_id: p for p in as_member}
    assert by_id[gran.id].constraints is None
    assert by_id[gran.id].effective_stairs_sensitivity is None
    assert by_id[gran.id].interests == {PlaceTag.HISTORY: 1}
    assert by_id[other.id].constraints is not None
    own = await preference_service.get_preferences(
        session, _membership(ANA, TripRole.MEMBER), gran.id
    )
    assert own.constraints is not None
    assert own.constraints.disability_note == "kolano"
    staff = await preference_service.get_preferences(
        session, _membership(BOB, TripRole.CO_HOST), gran.id
    )
    assert staff.constraints is not None
    one = await preference_service.get_preferences(
        session, _membership(BOB, TripRole.MEMBER), gran.id
    )
    assert one.constraints is None


@_sync
async def test_like_with_place_id_becomes_a_want_rating_not_json(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    me = _profile(user_sub=BOB)
    world = World(monkeypatch, me)
    place = world.add_place("Muzeum Bursztynu")
    body = PreferencesWrite.model_validate(
        {
            "example_places": [
                {"name": "x", "place_id": str(place), "verdict": "like"},
                {"name": "Pizzeria u Zenka", "verdict": "dislike"},
            ]
        }
    )
    saved = await preference_service.replace_preferences(
        session, _membership(BOB, TripRole.MEMBER), me.id, body
    )
    ((profile_id, place_id, rating),) = world.staged
    assert (profile_id, place_id, rating.value) == (me.id, place, RatingValue.WANT)
    assert rating.reason_code is None
    # Only the free-text entry is stored in the JSONB column.
    assert [e["name"] for e in world.upserts[0][1]["example_places"]] == [
        "Pizzeria u Zenka"
    ]
    # The read rebuilds the catalog entry from the rating, with the catalog name.
    assert {(e.name, e.place_id, e.verdict.value) for e in saved.example_places} == {
        ("Pizzeria u Zenka", None, "dislike"),
        ("Muzeum Bursztynu", place, "like"),
    }
    session.commit.assert_awaited_once()


@_sync
async def test_dislike_with_place_id_is_dont_want_with_reason_other(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    me = _profile(user_sub=BOB)
    world = World(monkeypatch, me)
    place = world.add_place()
    body = PreferencesWrite.model_validate(
        {
            "example_places": [
                {"name": "x", "place_id": str(place), "verdict": "dislike"}
            ]
        }
    )
    saved = await preference_service.replace_preferences(
        session, _membership(BOB, TripRole.MEMBER), me.id, body
    )
    assert world.staged[0][2].value is RatingValue.DONT_WANT
    assert world.staged[0][2].reason_code is ReasonCode.OTHER
    assert saved.example_places[0].verdict.value == "dislike"


@_sync
async def test_neutral_ratings_are_not_examples(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    me = _profile(user_sub=BOB)
    world = World(monkeypatch, me)
    world.ratings = [_rating(me, world.add_place(), RatingValue.NEUTRAL)]
    read = await preference_service.get_preferences(
        session, _membership(BOB, TripRole.MEMBER), me.id
    )
    assert read.example_places == []


@_sync
async def test_unknown_place_id_is_rejected_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    me = _profile(user_sub=BOB)
    world = World(monkeypatch, me)
    missing = uuid.uuid4()
    body = PreferencesWrite.model_validate(
        {"example_places": [{"name": "x", "place_id": str(missing), "verdict": "like"}]}
    )
    with pytest.raises(ExamplePlaceNotFoundError, match=str(missing)):
        await preference_service.replace_preferences(
            session, _membership(BOB, TripRole.MEMBER), me.id, body
        )
    assert world.upserts == world.staged == []
    session.commit.assert_not_awaited()


@_sync
async def test_member_cannot_write_someone_elses_or_an_unlinked_profile(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    other = _profile(user_sub=ANA)
    child = _profile(age=6)
    world = World(monkeypatch, other, child)
    for target in (other, child):
        with pytest.raises(ProfileForbiddenError):
            await preference_service.replace_preferences(
                session,
                _membership(BOB, TripRole.MEMBER),
                target.id,
                PreferencesWrite(),
            )
    assert world.upserts == []
    saved = await preference_service.replace_preferences(
        session, _membership(BOB, TripRole.CO_HOST), child.id, PreferencesWrite()
    )
    assert saved.importance_pool.model_dump() == {
        d.value: p for d, p in default_pool(AgeGroup.CHILD).items()
    }


def test_upsert_statement_is_one_on_conflict_update_with_explicit_updated_at() -> None:
    session = MagicMock(scalars=AsyncMock(return_value=MagicMock()))
    values = {
        "interests": {},
        "importance_pool": POOL,
        "constraints": {},
        "diet": {},
        "example_places": [],
        "min_tags": [],
    }
    asyncio.run(db.upsert_preferences(session, uuid.uuid4(), values, BOB))
    stmt = session.scalars.await_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (profile_id) DO UPDATE" in sql
    assert "updated_at=now()" in sql.replace(" ", "")
    assert "updated_by_sub=excluded.updated_by_sub" in sql.replace(" ", "")


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
    World(monkeypatch, other)
    url = path("put_preferences", trip_id=TRIP, profile_id=other.id)
    assert client.put(url, json={}).status_code == 403


def test_unknown_profile_gives_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    World(monkeypatch)
    url = path("get_preferences", trip_id=TRIP, profile_id=uuid.uuid4())
    assert client.get(url).status_code == 404
    assert client.put(url, json={}).status_code == 404


def test_unknown_place_gives_422_with_the_service_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    me = _profile(user_sub=BOB)
    World(monkeypatch, me)
    missing = uuid.uuid4()
    body = {
        "example_places": [{"name": "x", "place_id": str(missing), "verdict": "like"}]
    }
    response = client.put(
        path("put_preferences", trip_id=TRIP, profile_id=me.id), json=body
    )
    assert response.status_code == 422
    assert str(missing) in response.json()["detail"]


def _routes(profile_id: uuid.UUID) -> list[tuple[str, str, dict[str, object] | None]]:
    return [
        ("get", path("list_preferences", trip_id=TRIP), None),
        ("get", path("get_preferences", trip_id=TRIP, profile_id=profile_id), None),
        ("put", path("put_preferences", trip_id=TRIP, profile_id=profile_id), {}),
    ]


def test_every_route_is_404_for_outsiders_and_403_for_a_missing_role(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = uuid.uuid4()
    gone = trip_service.TripNotFoundError(str(TRIP))
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=gone))
    for method, url, body in _routes(pid):
        assert client.request(method, url, json=body).status_code == 404
    low = TripRoleError("Trip role 'member' required")
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=low))
    for method, url, body in _routes(pid):
        assert client.request(method, url, json=body).status_code == 403


def test_every_route_needs_the_feature_permission() -> None:
    app = create_app()
    authorize(app, AuthenticatedUser(sub=BOB), grants=[])
    with TestClient(app) as bare:
        for method, url, body in _routes(uuid.uuid4()):
            response = bare.request(method, url, json=body)
            assert response.status_code == 403
            assert "profiles.preferences" in response.text


def test_get_returns_defaults_over_http(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    me = _profile(user_sub=BOB)
    World(monkeypatch, me)
    body = client.get(path("get_preferences", trip_id=TRIP, profile_id=me.id)).json()
    assert body["filled"] is False
    assert sum(body["importance_pool"].values()) == 10
