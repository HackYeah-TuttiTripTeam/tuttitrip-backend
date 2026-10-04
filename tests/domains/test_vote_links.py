"""Voting links for the host: create, list, revoke and the aggregate result."""

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.places.schemas import PlaceRead
from tuttitrip.places.services import place_service
from tuttitrip.profiles.feedback.schemas import (
    RatingRead,
    RatingValue,
    ReasonCode,
    TripFeedback,
    VetoRead,
    link_author,
)
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.profiles.services.profile_service import ProfileNotFoundError
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.pagination.schemas import Page, PageParams, SortDir
from tuttitrip.shared.permissions import db as permission_db
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.models import AccessToken
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.shared.permissions.schemas import TokenScope
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripRoleError
from tuttitrip.voting.logic import summary
from tuttitrip.voting.schemas import (
    VoteSource,
    VoteSummaryFilters,
    VoteSummarySort,
)

HOST = AuthenticatedUser(sub="auth0|host")
TRIP = uuid.uuid4()
GRANDMA = uuid.uuid4()
ANN = uuid.uuid4()
MUSEUM = uuid.uuid4()
PARK = uuid.uuid4()
SESSION = AsyncMock()
NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


def _profile(profile_id: uuid.UUID, name: str, sub: str | None) -> ProfileRead:
    return ProfileRead.model_construct(id=profile_id, display_name=name, user_sub=sub)


PROFILES = [_profile(GRANDMA, "Babcia", None), _profile(ANN, "Ania", "auth0|ann")]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    application = create_app()
    authorize(application, HOST)
    application.dependency_overrides[get_session] = lambda: SESSION
    monkeypatch.setattr(
        profile_service, "list_profiles", AsyncMock(return_value=PROFILES)
    )

    def get_profile(_s: object, _m: object, profile_id: uuid.UUID) -> ProfileRead:
        for profile in PROFILES:
            if profile.id == profile_id:
                return profile
        raise ProfileNotFoundError(str(profile_id))

    monkeypatch.setattr(
        profile_service, "get_profile", AsyncMock(side_effect=get_profile)
    )
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _role(monkeypatch: pytest.MonkeyPatch, role: TripRole) -> None:
    def check(_s: object, _t: object, _sub: str, min_role: TripRole) -> TripMembership:
        if not role.satisfies(min_role):
            msg = f"Trip role '{min_role}' required (you are '{role}')"
            raise TripRoleError(msg)
        return TripMembership(trip_id=TRIP, sub=HOST.sub, role=role)

    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=check))


def _row(**overrides: object) -> AccessToken:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "token_hash": "a" * 64,
        "scope": TokenScope.VOTE,
        "trip_id": TRIP,
        "profile_id": GRANDMA,
        "expires_at": NOW + timedelta(days=1),
        "revoked_at": None,
        "last_used_at": None,
        "created_by": HOST.sub,
        "created_at": NOW,
    } | overrides
    return AccessToken(**values)


def test_a_co_host_creates_a_link_and_sees_the_token_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    inserted: list[AccessToken] = []
    revoke = AsyncMock()
    monkeypatch.setattr(permission_db, "revoke_active_tokens", revoke)
    monkeypatch.setattr(
        permission_db, "count_active_access_tokens", AsyncMock(return_value=0)
    )

    def insert(_session: object, row: AccessToken) -> AccessToken:
        row.id = uuid.uuid4()
        row.created_at = NOW
        inserted.append(row)
        return row

    monkeypatch.setattr(
        permission_db, "insert_access_token", AsyncMock(side_effect=insert)
    )
    response = client.post(
        path("create_vote_link", trip_id=TRIP), json={"profile_id": str(GRANDMA)}
    )
    assert response.status_code == 201
    body = response.json()
    (row,) = inserted
    assert (row.scope, row.trip_id, row.profile_id) == (TokenScope.VOTE, TRIP, GRANDMA)
    assert len(row.token_hash) == 64
    assert body["token"] not in {row.token_hash, str(row.id)}
    assert body["url"] == f"/glos#t={body['token']}"
    assert body["profile_name"] == "Babcia"
    assert body["state"] == "active"
    assert response.headers["cache-control"] == "no-store"
    revoke.assert_awaited_once()
    assert revoke.await_args is not None
    assert revoke.await_args.args[1:3] == (GRANDMA, TokenScope.VOTE)


def test_too_many_working_tokens_is_409_not_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    monkeypatch.setattr(permission_db, "revoke_active_tokens", AsyncMock())
    monkeypatch.setattr(
        permission_db, "count_active_access_tokens", AsyncMock(return_value=5)
    )
    response = client.post(
        path("create_vote_link", trip_id=TRIP), json={"profile_id": str(GRANDMA)}
    )
    assert response.status_code == 409


def test_a_plain_member_cannot_create_a_link(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.MEMBER)
    response = client.post(
        path("create_vote_link", trip_id=TRIP), json={"profile_id": str(GRANDMA)}
    )
    assert response.status_code == 403


def test_a_caller_without_the_permission_gets_403(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.HOST)
    authorize(app, HOST, [Grant(Feature.TRIPS_CORE, Access.WRITE)])
    response = client.post(
        path("create_vote_link", trip_id=TRIP), json={"profile_id": str(GRANDMA)}
    )
    assert response.status_code == 403
    assert "trips.vote_links:WRITE" in response.text


def test_a_profile_with_an_account_gets_no_link(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    response = client.post(
        path("create_vote_link", trip_id=TRIP), json={"profile_id": str(ANN)}
    )
    assert response.status_code == 409


def test_a_profile_of_another_trip_is_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    response = client.post(
        path("create_vote_link", trip_id=TRIP), json={"profile_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404


def test_the_list_is_a_page_without_the_secret(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    live = _row()
    gone = _row(revoked_at=NOW)
    page = Page.of([live, gone], 2, PageParams())
    monkeypatch.setattr(
        permission_db, "select_trip_tokens_page", AsyncMock(return_value=page)
    )
    response = client.get(path("list_vote_links", trip_id=TRIP), params={"size": 10})
    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["page"], body["pages"]) == (2, 1, 1)
    assert [item["state"] for item in body["items"]] == ["active", "revoked"]
    assert {item["profile_name"] for item in body["items"]} == {"Babcia"}
    assert set(body["items"][0]) == {
        "id",
        "profile_id",
        "profile_name",
        "state",
        "created_at",
        "expires_at",
        "revoked_at",
        "last_used_at",
    }
    assert "a" * 64 not in response.text


def test_the_list_filters_are_validated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    response = client.get(
        path("list_vote_links", trip_id=TRIP), params={"state": "nonsense"}
    )
    assert response.status_code == 422


def test_revoking_sets_revoked_at_and_is_idempotent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    row = _row()
    monkeypatch.setattr(permission_db, "select_trip_token", AsyncMock(return_value=row))
    url = path("revoke_vote_link", trip_id=TRIP, link_id=row.id)
    first = client.delete(url)
    assert first.status_code == 200
    assert first.json()["state"] == "revoked"
    revoked_at = row.revoked_at
    assert client.delete(url).status_code == 200
    assert row.revoked_at == revoked_at


def test_an_unknown_or_foreign_link_is_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    monkeypatch.setattr(
        permission_db, "select_trip_token", AsyncMock(return_value=None)
    )
    response = client.delete(
        path("revoke_vote_link", trip_id=TRIP, link_id=uuid.uuid4())
    )
    assert response.status_code == 404


# --- aggregate result -----------------------------------------------------------


def _rating(
    profile: uuid.UUID,
    place: uuid.UUID,
    value: RatingValue,
    by: str,
    reason: ReasonCode | None = None,
) -> RatingRead:
    return RatingRead(
        trip_id=TRIP,
        profile_id=profile,
        place_id=place,
        value=value,
        reason_code=reason,
        updated_by_sub=by,
        updated_at=NOW,
    )


def _veto(
    profile: uuid.UUID, place: uuid.UUID, by: str, *, on_behalf: bool
) -> VetoRead:
    return VetoRead(
        id=uuid.uuid4(),
        trip_id=TRIP,
        profile_id=profile,
        place_id=place,
        created_by_sub=by,
        on_behalf=on_behalf,
        created_at=NOW,
        revoked_at=None,
        revoked_by_sub=None,
    )


LINK = link_author(uuid.uuid4())

FEEDBACK = TripFeedback(
    ratings=[
        _rating(
            GRANDMA, MUSEUM, RatingValue.DONT_WANT, LINK, ReasonCode.TOO_HARD_FOR_CHILD
        ),
        _rating(GRANDMA, PARK, RatingValue.WANT, LINK),
        _rating(ANN, MUSEUM, RatingValue.WANT, "auth0|ann"),
        _rating(ANN, PARK, RatingValue.NEUTRAL, HOST.sub),
    ],
    vetoes=[_veto(GRANDMA, MUSEUM, LINK, on_behalf=False)],
)


def _fake_places(_s: object, ids: object) -> dict[uuid.UUID, PlaceRead]:
    names = {MUSEUM: "Muzeum", PARK: "Park"}
    return {
        i: PlaceRead.model_construct(id=i, name=names[i])
        for i in cast("set[uuid.UUID]", ids)
    }


@pytest.fixture
def voted(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    def arrange() -> None:
        monkeypatch.setattr(
            feedback_service, "list_for_trip", AsyncMock(return_value=FEEDBACK)
        )
        monkeypatch.setattr(
            place_service, "get_places", AsyncMock(side_effect=_fake_places)
        )

    return arrange


def test_the_host_sees_who_voted_what_with_the_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, voted: Callable[[], None]
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    voted()
    response = client.get(path("read_vote_summary", trip_id=TRIP))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    museum, park = body["items"]
    assert museum["place_name"] == "Muzeum"
    assert (museum["want"], museum["dont_want"], museum["veto_count"]) == (1, 1, 1)
    against = next(v for v in museum["votes"] if v["value"] == "dont_want")
    assert (against["display_name"], against["reason_code"], against["source"]) == (
        "Babcia",
        "too_hard_for_child",
        "link",
    )
    assert museum["vetoes"][0]["source"] == "link"
    assert museum["vetoes"][0]["display_name"] == "Babcia"
    neutral = next(v for v in park["votes"] if v["value"] == "neutral")
    assert neutral["source"] == "host"
    assert LINK not in response.text


def test_the_summary_filters_by_veto_and_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, voted: Callable[[], None]
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    voted()
    url = path("read_vote_summary", trip_id=TRIP)
    only_vetoed = client.get(url, params={"has_veto": "true"}).json()
    assert [i["place_name"] for i in only_vetoed["items"]] == ["Muzeum"]
    no_veto = client.get(url, params={"has_veto": "false"}).json()
    assert [i["place_name"] for i in no_veto["items"]] == ["Park"]
    from_app = client.get(url, params={"source": "app"}).json()
    assert [i["place_name"] for i in from_app["items"]] == ["Muzeum"]


def test_the_summary_pages_and_sorts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, voted: Callable[[], None]
) -> None:
    _role(monkeypatch, TripRole.CO_HOST)
    voted()
    url = path("read_vote_summary", trip_id=TRIP)
    second = client.get(
        url, params={"size": 1, "page": 2, "sort": "name", "dir": "asc"}
    ).json()
    assert (second["total"], second["pages"]) == (2, 2)
    assert [i["place_name"] for i in second["items"]] == ["Park"]
    past_end = client.get(url, params={"page": 9}).json()
    assert (past_end["items"], past_end["total"]) == ([], 2)


def test_a_plain_member_cannot_read_the_summary(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _role(monkeypatch, TripRole.MEMBER)
    assert client.get(path("read_vote_summary", trip_id=TRIP)).status_code == 403


# --- pure logic ------------------------------------------------------------------


def test_sources_of_ratings_and_vetoes() -> None:
    people = {
        GRANDMA: summary.Person("Babcia", None),
        ANN: summary.Person("Ania", "auth0|ann"),
    }
    own = _rating(ANN, PARK, RatingValue.WANT, "auth0|ann")
    assert summary.rating_source(own, people[ANN]) is VoteSource.APP
    behalf = _rating(GRANDMA, PARK, RatingValue.WANT, HOST.sub)
    assert summary.rating_source(behalf, people[GRANDMA]) is VoteSource.HOST
    assert summary.rating_source(behalf, None) is VoteSource.HOST
    linked = _rating(GRANDMA, PARK, RatingValue.WANT, LINK)
    assert summary.rating_source(linked, people[GRANDMA]) is VoteSource.LINK
    assert summary.veto_source(_veto(ANN, PARK, "auth0|ann", on_behalf=False)) is (
        VoteSource.APP
    )
    assert summary.veto_source(_veto(GRANDMA, PARK, HOST.sub, on_behalf=True)) is (
        VoteSource.HOST
    )
    assert summary.veto_source(_veto(GRANDMA, PARK, LINK, on_behalf=False)) is (
        VoteSource.LINK
    )


def test_the_last_writer_decides_the_source() -> None:
    people = {GRANDMA: summary.Person("Babcia", None)}
    link_vote = _rating(GRANDMA, PARK, RatingValue.DONT_WANT, LINK, ReasonCode.OTHER)
    # The rating row is one per person and place: a host overwriting it replaces
    # the author, so the summary then reports the host.
    overwritten = _rating(GRANDMA, PARK, RatingValue.WANT, HOST.sub)
    before = summary.summarize([link_vote], [], people, {PARK: "Park"})
    after = summary.summarize([overwritten], [], people, {PARK: "Park"})
    assert before[0].votes[0].source is VoteSource.LINK
    assert after[0].votes[0].source is VoteSource.HOST


def test_a_place_with_only_a_veto_is_listed() -> None:
    rows = summary.summarize(
        [], [_veto(GRANDMA, MUSEUM, LINK, on_behalf=False)], {}, {MUSEUM: "Muzeum"}
    )
    (row,) = rows
    assert (row.want, row.dont_want, row.neutral, row.veto_count) == (0, 0, 0, 1)
    assert row.vetoes[0].display_name == summary.UNKNOWN_PERSON


def test_ordering_is_stable_in_both_directions() -> None:
    rows = summary.summarize(
        FEEDBACK.ratings, FEEDBACK.vetoes, {}, {MUSEUM: "Muzeum", PARK: "Park"}
    )
    by_veto = summary.ordered(rows, VoteSummarySort.VETO, SortDir.DESC)
    assert [r.place_name for r in by_veto] == ["Muzeum", "Park"]
    by_name = summary.ordered(rows, VoteSummarySort.NAME, SortDir.DESC)
    assert [r.place_name for r in by_name] == ["Park", "Muzeum"]
    assert summary.matches(rows[0], VoteSummaryFilters())
