"""Rating places and vetoes through a voting link, without an account."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.places.schemas import PlaceRead
from tuttitrip.places.services import place_service
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.plans.services.plan_service import PlanInputError
from tuttitrip.profiles.feedback.schemas import (
    RatingRead,
    RatingValue,
    ReasonCode,
    TripFeedback,
    VetoRead,
    link_author,
)
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.profiles.feedback.services.feedback_service import (
    FeedbackPlaceNotFoundError,
    VetoNotFoundError,
)
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions import db as permission_db
from tuttitrip.shared.permissions.models import AccessToken
from tuttitrip.shared.permissions.schemas import TokenScope
from tuttitrip.shared.permissions.services import token_service
from tuttitrip.trips.schemas import TripRead
from tuttitrip.trips.services import trip_service

TRIP = uuid.uuid4()
GRANDMA = uuid.uuid4()
MUSEUM = uuid.uuid4()
PARK = uuid.uuid4()
GONE = uuid.uuid4()
TOKEN = "t" * 43
HEADERS = {"X-Access-Token": TOKEN}
TOKEN_ID = uuid.uuid4()
NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


def _row(scope: TokenScope = TokenScope.VOTE) -> AccessToken:
    return AccessToken(
        id=TOKEN_ID,
        token_hash=token_service.hash_token(TOKEN),
        scope=scope,
        trip_id=TRIP,
        profile_id=GRANDMA,
        expires_at=datetime.now(UTC) + timedelta(days=1),
        revoked_at=None,
        last_used_at=None,
        created_by="auth0|host",
        created_at=NOW,
    )


def _place(place_id: uuid.UUID, name: str) -> PlaceRead:
    return PlaceRead.model_construct(
        id=place_id, name=name, category="museum", address="ul. Długa 1"
    )


def _rating(
    place_id: uuid.UUID, value: RatingValue, reason: ReasonCode | None
) -> RatingRead:
    return RatingRead.model_construct(
        trip_id=TRIP,
        profile_id=GRANDMA,
        place_id=place_id,
        value=value,
        reason_code=reason,
        updated_by_sub=link_author(TOKEN_ID),
        updated_at=NOW,
    )


def _veto(place_id: uuid.UUID) -> VetoRead:
    return VetoRead.model_construct(
        id=uuid.uuid4(),
        trip_id=TRIP,
        profile_id=GRANDMA,
        place_id=place_id,
        created_by_sub=link_author(TOKEN_ID),
        on_behalf=False,
        created_at=NOW,
        revoked_at=None,
        revoked_by_sub=None,
    )


@dataclass(frozen=True)
class Call:
    """One voting route with a valid request body."""

    route: str
    method: str
    kwargs: dict[str, object]
    body: dict[str, str] | None

    def send(self, client: TestClient, headers: dict[str, str]) -> Response:
        return client.request(
            self.method,
            path(self.route, **self.kwargs),
            headers=headers,
            json=self.body,
        )


CASES = [
    Call("read_vote_session", "get", {}, None),
    Call("rate_place_by_link", "put", {"place_id": PARK}, {"value": "want"}),
    Call("veto_place_by_link", "post", {}, {"place_id": str(MUSEUM)}),
    Call("withdraw_veto_by_link", "delete", {"veto_id": uuid.uuid4()}, None),
]


class World:
    """Mocked services around the voting routes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.feedback = TripFeedback(ratings=[], vetoes=[])
        self.planned = [MUSEUM, PARK]
        self.account: str | None = None
        self.generate = AsyncMock()
        self.veto = _veto(MUSEUM)
        self.rate_by_author = AsyncMock(
            return_value=_rating(PARK, RatingValue.WANT, None)
        )
        self.veto_by_author = AsyncMock(return_value=self.veto)
        self.revoke_veto_by_author = AsyncMock(return_value=self.veto)
        places = {MUSEUM: _place(MUSEUM, "Muzeum"), PARK: _place(PARK, "Park")}

        def get_place(_s: object, place_id: uuid.UUID) -> PlaceRead:
            if place_id not in places:
                raise FeedbackPlaceNotFoundError(str(place_id))
            return places[place_id]

        monkeypatch.setattr(
            token_service, "touch", AsyncMock()
        )  # the use of the token is not under test
        monkeypatch.setattr(
            permission_db,
            "select_access_token_by_hash",
            AsyncMock(side_effect=lambda *_a: self.token),
        )
        monkeypatch.setattr(
            profile_service, "get_profile", AsyncMock(side_effect=self._profile)
        )
        monkeypatch.setattr(
            trip_service,
            "get_trip",
            AsyncMock(return_value=TripRead.model_construct(name="Kraków")),
        )
        monkeypatch.setattr(
            feedback_service,
            "list_for_profile",
            AsyncMock(side_effect=lambda *_a: self.feedback),
        )
        monkeypatch.setattr(feedback_service, "rate_by_author", self.rate_by_author)
        monkeypatch.setattr(feedback_service, "veto_by_author", self.veto_by_author)
        monkeypatch.setattr(
            feedback_service, "revoke_veto_by_author", self.revoke_veto_by_author
        )
        monkeypatch.setattr(
            plan_service,
            "latest_place_ids",
            AsyncMock(side_effect=lambda *_a: self.planned),
        )
        monkeypatch.setattr(plan_service, "generate_plan", self.generate)
        monkeypatch.setattr(
            place_service, "get_place", AsyncMock(side_effect=get_place)
        )
        monkeypatch.setattr(
            place_service,
            "get_places",
            AsyncMock(
                side_effect=lambda _s, ids: {i: places[i] for i in ids if i in places}
            ),
        )
        self.token: AccessToken | None = _row()

    def _profile(self, *_args: object) -> ProfileRead:
        return ProfileRead.model_construct(
            id=GRANDMA, display_name="Babcia", user_sub=self.account
        )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    return World(monkeypatch)


def _session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client(world: World) -> Iterator[TestClient]:
    assert world.token is not None
    app = create_app()
    app.dependency_overrides[get_session] = _session
    with TestClient(app) as test_client:
        yield test_client


def test_the_session_has_the_plan_places_and_only_my_answers(
    client: TestClient, world: World
) -> None:
    world.feedback = TripFeedback(
        ratings=[_rating(PARK, RatingValue.DONT_WANT, ReasonCode.TOO_FAR)],
        vetoes=[_veto(MUSEUM)],
    )
    response = client.get(path("read_vote_session"), headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["trip_name"] == "Kraków"
    assert body["profile_name"] == "Babcia"
    museum, park = body["places"]
    assert museum["veto_id"] == str(world.feedback.vetoes[0].id)
    assert park["rating"] == "dont_want"
    assert park["reason_code"] == "too_far"
    assert {"description", "photo_url"} <= museum.keys()
    assert "other" not in response.text


def test_a_place_the_plan_dropped_still_comes_back_with_my_veto(
    client: TestClient, world: World
) -> None:
    world.planned = [PARK]
    world.feedback = TripFeedback(ratings=[], vetoes=[_veto(MUSEUM)])
    places = client.get(path("read_vote_session"), headers=HEADERS).json()["places"]
    assert [(p["name"], p["in_plan"]) for p in places] == [
        ("Park", True),
        ("Muzeum", False),
    ]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.route)
def test_a_dead_token_is_404_and_a_missing_one_401(
    client: TestClient, world: World, case: Call
) -> None:
    world.token = None
    dead = case.send(client, HEADERS)
    assert dead.status_code == 404
    assert dead.headers["cache-control"] == "no-store"
    assert case.send(client, {}).status_code == 401
    assert TOKEN not in dead.text


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.route)
def test_the_link_stops_working_once_the_profile_has_an_account(
    client: TestClient, world: World, case: Call
) -> None:
    world.account = "auth0|grandma"
    response = case.send(client, HEADERS)
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    world.rate_by_author.assert_not_awaited()
    world.veto_by_author.assert_not_awaited()
    world.generate.assert_not_awaited()


def test_rating_dont_want_without_a_reason_is_422(
    client: TestClient, world: World
) -> None:
    response = client.put(
        path("rate_place_by_link", place_id=PARK),
        headers=HEADERS,
        json={"value": "dont_want"},
    )
    assert response.status_code == 422
    world.rate_by_author.assert_not_awaited()


def test_a_rating_is_stored_with_the_link_as_author_and_does_not_replan(
    client: TestClient, world: World
) -> None:
    world.feedback = TripFeedback(
        ratings=[_rating(PARK, RatingValue.WANT, None)], vetoes=[]
    )
    response = client.put(
        path("rate_place_by_link", place_id=PARK),
        headers=HEADERS,
        json={"value": "want"},
    )
    assert response.status_code == 200
    assert response.json()["rating"] == "want"
    assert world.rate_by_author.await_args is not None
    actor = world.rate_by_author.await_args.args[1]
    assert (actor.trip_id, actor.profile_id) == (TRIP, GRANDMA)
    assert actor.author == f"link:{TOKEN_ID}"
    world.generate.assert_not_awaited()


def test_a_veto_is_stored_and_the_plan_is_recomputed_in_the_request(
    client: TestClient, world: World
) -> None:
    world.feedback = TripFeedback(ratings=[], vetoes=[world.veto])
    response = client.post(
        path("veto_place_by_link"), headers=HEADERS, json={"place_id": str(MUSEUM)}
    )
    assert response.status_code == 200
    assert response.json()["veto_id"] == str(world.veto.id)
    assert world.generate.await_args is not None
    membership = world.generate.await_args.args[1]
    assert (membership.trip_id, membership.sub) == (TRIP, f"link:{TOKEN_ID}")
    assert world.veto_by_author.await_args is not None
    assert world.veto_by_author.await_args.args[1].author == f"link:{TOKEN_ID}"


def test_a_veto_on_a_trip_that_cannot_be_planned_yet_still_succeeds(
    client: TestClient, world: World
) -> None:
    world.generate.side_effect = PlanInputError("no city")
    world.feedback = TripFeedback(ratings=[], vetoes=[world.veto])
    response = client.post(
        path("veto_place_by_link"), headers=HEADERS, json={"place_id": str(MUSEUM)}
    )
    assert response.status_code == 200


def test_an_unknown_place_is_404(client: TestClient, world: World) -> None:
    world.veto_by_author.side_effect = FeedbackPlaceNotFoundError("x")
    response = client.post(
        path("veto_place_by_link"), headers=HEADERS, json={"place_id": str(GONE)}
    )
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_withdrawing_a_veto_recomputes_and_clears_veto_id(
    client: TestClient, world: World
) -> None:
    response = client.delete(
        path("withdraw_veto_by_link", veto_id=world.veto.id), headers=HEADERS
    )
    assert response.status_code == 200
    assert response.json()["veto_id"] is None
    world.generate.assert_awaited_once()


def test_withdrawing_somebody_elses_veto_is_404(
    client: TestClient, world: World
) -> None:
    world.revoke_veto_by_author.side_effect = VetoNotFoundError("x")
    response = client.delete(
        path("withdraw_veto_by_link", veto_id=uuid.uuid4()), headers=HEADERS
    )
    assert response.status_code == 404
    world.generate.assert_not_awaited()
