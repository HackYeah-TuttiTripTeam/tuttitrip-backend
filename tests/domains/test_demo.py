"""Jury one-link login: token check, Auth0 grant, rate limit, sample data."""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable, Iterator
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx2 import Response as Reply

from tests.shared.paths import path
from tuttitrip.demo import api as demo_api
from tuttitrip.demo.api import get_client_factory, get_rate_limiter
from tuttitrip.demo.logic.dataset import DEMO_TRIPS
from tuttitrip.demo.logic.rate_limit import RateLimiter
from tuttitrip.demo.logic.token import token_matches
from tuttitrip.demo.services import demo_service, seed_command
from tuttitrip.main import create_app
from tuttitrip.shared.config.settings import DemoSettings, Settings
from tuttitrip.trips.schemas import TripRole

TOKEN = "t0ken-for-tests-only-not-a-real-secret"
ACCOUNT_PASSWORD = "p4ss-for-tests-only"
SHA = hashlib.sha256(TOKEN.encode()).hexdigest()


def _demo(**overrides: object) -> DemoSettings:
    values: dict[str, object] = {
        "token_sha256": SHA,
        "username": "demo@example.test",
        "password": ACCOUNT_PASSWORD,
        "client_id": "client-123",
    }
    return DemoSettings.model_validate(values | overrides)


class Auth0Stub:
    """Fake Auth0 token endpoint that records what it was sent."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "access_denied"})
        return httpx.Response(
            200,
            json={
                "access_token": "access-jwt",
                "refresh_token": "refresh-xyz",
                "expires_in": 86400,
                "token_type": "Bearer",
            },
        )

    def factory(self) -> Callable[[], httpx.AsyncClient]:
        return lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(self),
            headers={"User-Agent": "TuttiTripBackend/test"},
        )


@pytest.fixture
def stub() -> Auth0Stub:
    return Auth0Stub()


@pytest.fixture
def client(stub: Auth0Stub, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    settings = Settings(demo=_demo())
    monkeypatch.setattr(demo_api, "get_settings", lambda: settings)
    get_rate_limiter.cache_clear()
    app = create_app()
    app.dependency_overrides[get_client_factory] = stub.factory
    yield TestClient(app)
    get_rate_limiter.cache_clear()


def _post(client: TestClient, token: object = TOKEN) -> Reply:
    return client.post(path("demo_login"), json={"token": token})


def test_a_good_token_returns_the_demo_accounts_tokens(
    client: TestClient, stub: Auth0Stub
) -> None:
    response = _post(client)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["access_token"] == "access-jwt"
    assert body["expires_in"] == 86400
    assert body["refresh_token"] is None  # offline_access is off
    (request,) = stub.requests
    sent = json.loads(request.content)
    assert sent["grant_type"].endswith("password-realm")
    assert sent["realm"] == "Username-Password-Authentication"
    assert sent["audience"] == "https://tuttitrip-api.gburek.app"
    assert sent["scope"] == "openid"
    assert "client_secret" not in sent
    assert "python" not in request.headers["user-agent"].lower()


def test_offline_access_asks_for_and_returns_a_refresh_token(
    stub: Auth0Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(demo=_demo(offline_access=True, client_secret="shh"))
    monkeypatch.setattr(demo_api, "get_settings", lambda: settings)
    get_rate_limiter.cache_clear()
    app = create_app()
    app.dependency_overrides[get_client_factory] = stub.factory
    response = _post(TestClient(app))
    assert response.json()["refresh_token"] == "refresh-xyz"
    sent = json.loads(stub.requests[0].content)
    assert sent["scope"] == "openid offline_access"
    assert sent["client_secret"] == "shh"
    get_rate_limiter.cache_clear()


@pytest.mark.parametrize("token", ["wrong", TOKEN.upper(), TOKEN + " "])
def test_a_wrong_token_is_a_plain_404_and_never_reaches_auth0(
    client: TestClient, stub: Auth0Stub, token: str
) -> None:
    response = _post(client, token)
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert stub.requests == []


def test_a_missing_token_is_a_422_without_calling_auth0(
    client: TestClient, stub: Auth0Stub
) -> None:
    assert client.post(path("demo_login"), json={}).status_code == 422
    assert stub.requests == []


def test_an_empty_sha_switches_the_route_off(
    stub: Auth0Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(demo=_demo(token_sha256=""))
    monkeypatch.setattr(demo_api, "get_settings", lambda: settings)
    get_rate_limiter.cache_clear()
    app = create_app()
    app.dependency_overrides[get_client_factory] = stub.factory
    web = TestClient(app)
    assert _post(web, "").status_code == 422
    assert _post(web, "anything").status_code == 404
    assert _post(web, TOKEN).status_code == 404
    assert stub.requests == []
    get_rate_limiter.cache_clear()


def test_the_route_needs_no_authentication(client: TestClient) -> None:
    assert "authorization" not in {k.lower() for k in client.headers}
    assert _post(client).status_code == 200


def test_auth0_failure_is_a_502_that_leaks_nothing(
    client: TestClient, stub: Auth0Stub, caplog: pytest.LogCaptureFixture
) -> None:
    stub.status = 403
    with caplog.at_level("DEBUG"):
        response = _post(client)
    assert response.status_code == 502
    assert response.headers["cache-control"] == "no-store"
    logs = caplog.text + response.text
    for secret in (TOKEN, ACCOUNT_PASSWORD, "demo@example.test"):
        assert secret not in logs


def test_no_log_line_holds_the_token_or_credentials(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("DEBUG"):
        _post(client)
        _post(client, "wrong-token-value")
    for secret in (TOKEN, "wrong-token-value", ACCOUNT_PASSWORD):
        assert secret not in caplog.text


def test_one_address_is_limited_per_minute(
    stub: Auth0Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(demo=_demo(rate_limit_per_minute=2))
    monkeypatch.setattr(demo_api, "get_settings", lambda: settings)
    get_rate_limiter.cache_clear()
    app = create_app()
    app.dependency_overrides[get_client_factory] = stub.factory
    web = TestClient(app)
    other = {"cf-connecting-ip": "203.0.113.9"}
    assert [_post(web, "x").status_code for _ in range(3)] == [404, 404, 429]
    limited = _post(web)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    # Another address has its own budget.
    assert (
        web.post(path("demo_login"), json={"token": TOKEN}, headers=other).status_code
        == 200
    )
    get_rate_limiter.cache_clear()


def test_the_rate_limiter_forgets_old_hits() -> None:
    now = [0.0]
    limiter = RateLimiter(2, clock=lambda: now[0])
    assert [limiter.allow("a") for _ in range(3)] == [True, True, False]
    now[0] = 61.0
    assert limiter.allow("a")


def test_token_check_is_exact_and_empty_means_off() -> None:
    assert token_matches(TOKEN, SHA)
    assert token_matches(TOKEN, SHA.upper())
    assert not token_matches(TOKEN, "")
    assert not token_matches("", "")
    assert not token_matches(TOKEN + "x", SHA)


def test_the_sample_set_has_four_cities_and_the_family_trip() -> None:
    slugs = {t.city_slug for t in DEMO_TRIPS}
    assert slugs == {"warszawa", "gdansk", "krakow", "berlin"}
    warszawa = DEMO_TRIPS[-1]  # created last, so it is the newest
    assert warszawa.city_slug == "warszawa"
    assert [p.age for p in warszawa.people] == [38, 6, 13, 72]
    assert warszawa.hard_amenities
    for trip in DEMO_TRIPS:
        payload = trip.create_payload(date(2026, 10, 4))
        assert payload.start_date is not None
        assert payload.end_date is not None
        assert payload.start_date > date(2026, 10, 4)
        for person in trip.people:
            assert person.preferences().importance_pool is not None


def _trip_services(monkeypatch: pytest.MonkeyPatch, owned: int) -> SimpleNamespace:
    """Replace the other domains' services with recording doubles."""
    existing = [
        SimpleNamespace(id=f"old-{i}", my_role=TripRole.HOST) for i in range(owned)
    ]
    created = SimpleNamespace(id="new")
    profile = SimpleNamespace(id=uuid.uuid4())
    fakes = SimpleNamespace(
        trips=SimpleNamespace(
            list_trips=AsyncMock(return_value=existing),
            get_membership=AsyncMock(return_value=MagicMock()),
            delete_trip=AsyncMock(),
            create_trip=AsyncMock(return_value=created),
        ),
        profiles=SimpleNamespace(
            list_profiles=AsyncMock(return_value=[profile]),
            update_profile=AsyncMock(return_value=profile),
            create_profile=AsyncMock(return_value=profile),
            set_weights=AsyncMock(),
        ),
        preferences=SimpleNamespace(replace_preferences=AsyncMock()),
        requirements=SimpleNamespace(replace_requirements=AsyncMock()),
        places=SimpleNamespace(list_places=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(demo_service, "trip_service", fakes.trips)
    monkeypatch.setattr(demo_service, "profile_service", fakes.profiles)
    monkeypatch.setattr(demo_service, "preference_service", fakes.preferences)
    monkeypatch.setattr(demo_service, "requirements_service", fakes.requirements)
    monkeypatch.setattr(demo_service, "place_service", fakes.places)
    return fakes


def test_the_reset_replaces_the_accounts_trips_with_the_sample_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fakes = _trip_services(monkeypatch, owned=2)
    created = asyncio.run(
        demo_service.reset_demo_account(MagicMock(), "auth0|demo", date(2026, 10, 4))
    )
    assert created == len(DEMO_TRIPS) == fakes.trips.create_trip.await_count
    assert fakes.trips.delete_trip.await_count == 2
    people = sum(len(t.people) for t in DEMO_TRIPS)
    assert fakes.preferences.replace_preferences.await_count == people
    assert fakes.profiles.create_profile.await_count == people - len(DEMO_TRIPS)
    assert fakes.profiles.set_weights.await_count == 1
    owners = {call.args[1] for call in fakes.trips.create_trip.await_args_list}
    assert owners == {"auth0|demo"}


def test_the_reset_leaves_trips_the_account_only_joined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fakes = _trip_services(monkeypatch, owned=0)
    fakes.trips.list_trips.return_value = [
        SimpleNamespace(id="theirs", my_role=TripRole.MEMBER)
    ]
    asyncio.run(demo_service.reset_demo_account(MagicMock(), "auth0|demo"))
    fakes.trips.delete_trip.assert_not_awaited()


def test_the_command_does_nothing_when_the_demo_is_off() -> None:
    assert asyncio.run(seed_command.run(Settings(demo=_demo(token_sha256="")))) == 0
