"""Place ratings and vetoes: reason rules, who may act for whom, active vetoes."""

import asyncio
import functools
import uuid
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Index
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateIndex

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.places.services.place_service import PlaceNotFoundError
from tuttitrip.planning.plans.schemas import ReasonCode as PlanReasonCode
from tuttitrip.profiles.feedback import db
from tuttitrip.profiles.feedback.models import PlaceRating, PlaceVeto
from tuttitrip.profiles.feedback.schemas import (
    RatingRead,
    RatingUpdate,
    RatingValue,
    ReasonCode,
    VetoCreate,
)
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.profiles.feedback.services.feedback_service import (
    ACTIVE_VETO_INDEX,
    FeedbackForbiddenError,
    FeedbackPlaceNotFoundError,
    ProfileNotFoundError,
    VetoExistsError,
    VetoNotFoundError,
)
from tuttitrip.profiles.models import Profile
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError

TRIP = uuid.uuid4()
PLACE = uuid.uuid4()
HOST = "auth0|host"
GRANNY = "auth0|granny"
MEMBER = "auth0|member"
NOW = datetime(2026, 10, 3, tzinfo=UTC)


def _sync[**P](test: Callable[P, Coroutine[None, None, None]]) -> Callable[P, None]:
    @functools.wraps(test)
    def run(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(test(*args, **kwargs))

    return run


def _membership(sub: str, role: TripRole) -> TripMembership:
    return TripMembership(trip_id=TRIP, sub=sub, role=role)


def _profile(user_sub: str | None) -> Profile:
    profile = Profile()
    profile.id = uuid.uuid4()
    profile.trip_id = TRIP
    profile.user_sub = user_sub
    return profile


def _rating(profile_id: uuid.UUID, value: str, reason: str | None) -> PlaceRating:
    return PlaceRating(
        trip_id=TRIP,
        profile_id=profile_id,
        place_id=PLACE,
        value=value,
        reason_code=reason,
        updated_by_sub=HOST,
        updated_at=NOW,
    )


def _veto(profile: Profile, *, on_behalf: bool, revoked: bool = False) -> PlaceVeto:
    return PlaceVeto(
        id=uuid.uuid4(),
        trip_id=TRIP,
        profile_id=profile.id,
        place_id=PLACE,
        created_by_sub=HOST,
        on_behalf=on_behalf,
        created_at=NOW,
        revoked_at=NOW if revoked else None,
    )


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def granny(monkeypatch: pytest.MonkeyPatch) -> Profile:
    """A profile linked to the account GRANNY; the place exists in the catalog."""
    profile = _profile(GRANNY)
    monkeypatch.setattr(
        feedback_service.profiles_db, "select_profile", AsyncMock(return_value=profile)
    )
    monkeypatch.setattr(feedback_service.place_service, "get_place", AsyncMock())
    return profile


# --- schemas -------------------------------------------------------------


def test_reason_codes_are_the_plan_contract_enum() -> None:
    assert ReasonCode is PlanReasonCode


def test_dont_want_without_a_reason_is_rejected() -> None:
    with pytest.raises(ValidationError, match="reason_code is required"):
        RatingUpdate(value=RatingValue.DONT_WANT)


@pytest.mark.parametrize("value", [RatingValue.WANT, RatingValue.NEUTRAL])
def test_a_reason_with_want_or_neutral_is_rejected(value: RatingValue) -> None:
    with pytest.raises(ValidationError, match="only for dont_want"):
        RatingUpdate(value=value, reason_code=ReasonCode.TOO_FAR)


def test_every_reason_is_accepted_for_dont_want() -> None:
    for reason in ReasonCode:
        assert RatingUpdate(value=RatingValue.DONT_WANT, reason_code=reason)


# --- ratings -------------------------------------------------------------


@_sync
async def test_member_rates_their_own_profile(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock, granny: Profile
) -> None:
    upsert = AsyncMock(
        return_value=_rating(granny.id, "dont_want", ReasonCode.TOO_FAR.value)
    )
    monkeypatch.setattr(db, "upsert_rating", upsert)
    read = await feedback_service.rate_place(
        session,
        _membership(GRANNY, TripRole.MEMBER),
        granny.id,
        PLACE,
        RatingUpdate(value=RatingValue.DONT_WANT, reason_code=ReasonCode.TOO_FAR),
    )
    saved = upsert.call_args.args[1]
    assert (saved.value, saved.reason_code) == ("dont_want", "too_far")
    assert saved.updated_by_sub == GRANNY
    assert read.value is RatingValue.DONT_WANT
    assert read.value is RatingValue.DONT_WANT
    session.commit.assert_awaited_once()


@_sync
async def test_member_cannot_rate_for_another_person(
    session: AsyncMock, granny: Profile
) -> None:
    with pytest.raises(FeedbackForbiddenError):
        await feedback_service.rate_place(
            session,
            _membership(MEMBER, TripRole.MEMBER),
            granny.id,
            PLACE,
            RatingUpdate(value=RatingValue.WANT),
        )


@_sync
async def test_co_host_rates_for_someone_without_an_account(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock
) -> None:
    child = _profile(None)
    monkeypatch.setattr(
        feedback_service.profiles_db, "select_profile", AsyncMock(return_value=child)
    )
    monkeypatch.setattr(feedback_service.place_service, "get_place", AsyncMock())
    monkeypatch.setattr(
        db, "upsert_rating", AsyncMock(return_value=_rating(child.id, "want", None))
    )
    read = await feedback_service.rate_place(
        session,
        _membership(HOST, TripRole.CO_HOST),
        child.id,
        PLACE,
        RatingUpdate(value=RatingValue.WANT),
    )
    assert read.value is RatingValue.WANT


@_sync
async def test_unknown_profile_and_place_are_not_found(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock, granny: Profile
) -> None:
    body = RatingUpdate(value=RatingValue.NEUTRAL)
    host = _membership(HOST, TripRole.HOST)
    monkeypatch.setattr(
        feedback_service.place_service,
        "get_place",
        AsyncMock(side_effect=PlaceNotFoundError),
    )
    with pytest.raises(FeedbackPlaceNotFoundError):
        await feedback_service.rate_place(session, host, granny.id, PLACE, body)
    monkeypatch.setattr(
        feedback_service.profiles_db, "select_profile", AsyncMock(return_value=None)
    )
    with pytest.raises(ProfileNotFoundError):
        await feedback_service.rate_place(session, host, granny.id, PLACE, body)


# --- vetoes --------------------------------------------------------------


@_sync
async def test_host_vetoes_on_behalf_of_granny_and_the_author_is_stored(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock, granny: Profile
) -> None:
    inserted = AsyncMock(side_effect=lambda _s, veto: veto)
    monkeypatch.setattr(db, "insert_veto", inserted)
    session.refresh.side_effect = lambda veto: (
        setattr(veto, "id", uuid.uuid4()),
        setattr(veto, "created_at", NOW),
    )
    read = await feedback_service.create_veto(
        session,
        _membership(HOST, TripRole.HOST),
        VetoCreate(profile_id=granny.id, place_id=PLACE),
    )
    assert (read.on_behalf, read.created_by_sub) == (True, HOST)
    assert read.revoked_at is None


@_sync
async def test_own_veto_is_not_on_behalf(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock, granny: Profile
) -> None:
    monkeypatch.setattr(db, "insert_veto", AsyncMock(side_effect=lambda _s, veto: veto))
    session.refresh.side_effect = lambda veto: (
        setattr(veto, "id", uuid.uuid4()),
        setattr(veto, "created_at", NOW),
    )
    read = await feedback_service.create_veto(
        session,
        _membership(GRANNY, TripRole.MEMBER),
        VetoCreate(profile_id=granny.id, place_id=PLACE),
    )
    assert (read.on_behalf, read.created_by_sub) == (False, GRANNY)


@_sync
async def test_member_cannot_veto_for_someone_else(
    session: AsyncMock, granny: Profile
) -> None:
    with pytest.raises(FeedbackForbiddenError):
        await feedback_service.create_veto(
            session,
            _membership(MEMBER, TripRole.MEMBER),
            VetoCreate(profile_id=granny.id, place_id=PLACE),
        )


@_sync
async def test_second_active_veto_is_a_conflict(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock, granny: Profile
) -> None:
    monkeypatch.setattr(db, "insert_veto", AsyncMock(side_effect=_unique_violation()))
    with pytest.raises(VetoExistsError):
        await feedback_service.create_veto(
            session,
            _membership(HOST, TripRole.HOST),
            VetoCreate(profile_id=granny.id, place_id=PLACE),
        )


@_sync
async def test_revoking_sets_revoked_at_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock, granny: Profile
) -> None:
    veto = _veto(granny, on_behalf=True)
    monkeypatch.setattr(db, "select_veto", AsyncMock(return_value=veto))
    host = _membership(HOST, TripRole.HOST)
    await feedback_service.revoke_veto(session, host, veto.id)
    assert veto.revoked_at is not None
    first = veto.revoked_at
    await feedback_service.revoke_veto(session, host, veto.id)
    assert veto.revoked_at == first
    session.commit.assert_awaited_once()


@_sync
async def test_revoking_needs_ownership_and_an_existing_veto(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock, granny: Profile
) -> None:
    veto = _veto(granny, on_behalf=True)
    monkeypatch.setattr(db, "select_veto", AsyncMock(return_value=veto))
    with pytest.raises(FeedbackForbiddenError):
        await feedback_service.revoke_veto(
            session, _membership(MEMBER, TripRole.MEMBER), veto.id
        )
    monkeypatch.setattr(db, "select_veto", AsyncMock(return_value=None))
    with pytest.raises(VetoNotFoundError):
        await feedback_service.revoke_veto(
            session, _membership(HOST, TripRole.HOST), veto.id
        )


@_sync
async def test_list_for_trip_returns_ratings_and_only_active_vetoes(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock, granny: Profile
) -> None:
    active = _veto(granny, on_behalf=True)
    monkeypatch.setattr(
        db,
        "select_ratings_by_trip",
        AsyncMock(return_value=[_rating(granny.id, "want", None)]),
    )
    by_trip = AsyncMock(return_value=[active])
    monkeypatch.setattr(db, "select_active_vetoes_by_trip", by_trip)
    feedback = await feedback_service.list_for_trip(session, TRIP)
    assert [r.value for r in feedback.ratings] == [RatingValue.WANT]
    assert [v.id for v in feedback.vetoes] == [active.id]
    by_trip.assert_awaited_once_with(session, TRIP)


# --- HTTP ----------------------------------------------------------------


def _unique_violation(name: str = "uq_place_vetoes_active") -> IntegrityError:
    return IntegrityError("insert", {}, Exception(f"duplicate key {name}"))


def _mock_session() -> AsyncMock:
    return AsyncMock()


def _client(
    monkeypatch: pytest.MonkeyPatch,
    role: TripRole = TripRole.MEMBER,
    grants: tuple[Grant, ...] = (Grant(Feature.PROFILES_FEEDBACK, Access.WRITE),),
) -> TestClient:
    app = create_app()
    authorize(app, AuthenticatedUser(sub=MEMBER), grants)
    app.dependency_overrides[get_session] = _mock_session
    monkeypatch.setattr(
        trip_service,
        "get_membership",
        AsyncMock(return_value=_membership(MEMBER, role)),
    )
    return TestClient(app)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with _client(monkeypatch) as test_client:
        yield test_client


def _rate_url(profile_id: uuid.UUID) -> str:
    return path("rate_place", trip_id=TRIP, profile_id=profile_id, place_id=PLACE)


def test_dont_want_without_a_reason_gives_422(client: TestClient) -> None:
    response = client.put(_rate_url(uuid.uuid4()), json={"value": "dont_want"})
    assert response.status_code == 422


def test_an_unknown_reason_gives_422(client: TestClient) -> None:
    body = {"value": "dont_want", "reason_code": "za_drogo"}
    assert client.put(_rate_url(uuid.uuid4()), json=body).status_code == 422


def test_rating_returns_the_stored_row(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, granny: Profile
) -> None:
    monkeypatch.setattr(
        db,
        "upsert_rating",
        AsyncMock(return_value=_rating(granny.id, "dont_want", "too_crowded")),
    )
    monkeypatch.setattr(
        feedback_service.profiles_db,
        "select_profile",
        AsyncMock(return_value=_profile(MEMBER)),
    )
    body = {"value": "dont_want", "reason_code": "too_crowded"}
    response = client.put(_rate_url(granny.id), json=body)
    assert response.status_code == 200
    assert response.json()["reason_code"] == "too_crowded"


def test_member_vetoing_for_another_person_gets_403(
    client: TestClient, granny: Profile
) -> None:
    body = {"profile_id": str(granny.id), "place_id": str(PLACE)}
    response = client.post(path("create_veto", trip_id=TRIP), json=body)
    assert response.status_code == 403


def test_unknown_place_gives_404_and_duplicate_veto_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, granny: Profile
) -> None:
    monkeypatch.setattr(
        feedback_service.profiles_db,
        "select_profile",
        AsyncMock(return_value=_profile(MEMBER)),
    )
    body = {"profile_id": str(granny.id), "place_id": str(PLACE)}
    url = path("create_veto", trip_id=TRIP)
    monkeypatch.setattr(
        feedback_service,
        "_require_place",
        AsyncMock(side_effect=FeedbackPlaceNotFoundError),
    )
    assert client.post(url, json=body).status_code == 404
    monkeypatch.setattr(feedback_service, "_require_place", AsyncMock())
    monkeypatch.setattr(db, "insert_veto", AsyncMock(side_effect=_unique_violation()))
    assert client.post(url, json=body).status_code == 409


def test_revoking_an_unknown_veto_gives_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "select_veto", AsyncMock(return_value=None))
    url = path("revoke_veto", trip_id=TRIP, veto_id=uuid.uuid4())
    assert client.delete(url).status_code == 404


def test_not_a_member_gets_404_on_every_route(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(side_effect=TripNotFoundError)
    )
    other = uuid.uuid4()
    assert client.get(path("list_ratings", trip_id=TRIP)).status_code == 404
    assert client.get(path("list_vetoes", trip_id=TRIP)).status_code == 404
    assert (
        client.delete(path("revoke_veto", trip_id=TRIP, veto_id=other)).status_code
        == 404
    )


def test_without_the_feedback_permission_every_route_gives_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _client(monkeypatch, grants=()) as client:
        other = uuid.uuid4()
        assert client.get(path("list_ratings", trip_id=TRIP)).status_code == 403
        assert client.get(path("list_vetoes", trip_id=TRIP)).status_code == 403
        assert client.put(_rate_url(other), json={"value": "want"}).status_code == 403
        assert (
            client.delete(path("revoke_veto", trip_id=TRIP, veto_id=other)).status_code
            == 403
        )


def test_read_permission_lists_but_cannot_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grants = (Grant(Feature.PROFILES_FEEDBACK, Access.READ),)
    monkeypatch.setattr(db, "select_ratings_by_trip", AsyncMock(return_value=[]))
    with _client(monkeypatch, grants=grants) as client:
        assert client.get(path("list_ratings", trip_id=TRIP)).json() == []
        assert (
            client.put(_rate_url(uuid.uuid4()), json={"value": "want"}).status_code
            == 403
        )


def test_rating_read_dto_exposes_the_author() -> None:
    dto = RatingRead.model_validate(_rating(uuid.uuid4(), "want", None))
    assert dto.updated_by_sub == HOST


def test_active_veto_index_is_partial_on_revoked_at() -> None:
    index = next(
        a
        for a in PlaceVeto.__table_args__
        if getattr(a, "name", "") == ACTIVE_VETO_INDEX
    )
    assert isinstance(index, Index)
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "UNIQUE INDEX" in ddl
    assert "WHERE revoked_at IS NULL" in ddl


@_sync
async def test_other_integrity_errors_are_not_reported_as_a_duplicate(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock, granny: Profile
) -> None:
    error = _unique_violation("fk_place_vetoes_place_id_places")
    monkeypatch.setattr(db, "insert_veto", AsyncMock(side_effect=error))
    with pytest.raises(IntegrityError):
        await feedback_service.create_veto(
            session,
            _membership(HOST, TripRole.HOST),
            VetoCreate(profile_id=granny.id, place_id=PLACE),
        )


@_sync
async def test_veto_again_after_a_revoke_is_created(
    monkeypatch: pytest.MonkeyPatch, session: AsyncMock, granny: Profile
) -> None:
    first = _veto(granny, on_behalf=True)
    monkeypatch.setattr(db, "select_veto", AsyncMock(return_value=first))
    host = _membership(HOST, TripRole.HOST)
    await feedback_service.revoke_veto(session, host, first.id)
    assert (first.revoked_at is not None, first.revoked_by_sub) == (True, HOST)
    monkeypatch.setattr(db, "insert_veto", AsyncMock(side_effect=lambda _s, v: v))
    session.refresh.side_effect = lambda veto: (
        setattr(veto, "id", uuid.uuid4()),
        setattr(veto, "created_at", NOW),
    )
    read = await feedback_service.create_veto(
        session, host, VetoCreate(profile_id=granny.id, place_id=PLACE)
    )
    assert read.revoked_at is None
