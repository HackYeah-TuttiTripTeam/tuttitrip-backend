"""Jury one-link login: token check, Auth0 grant, rate limit, sample data."""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable, Iterator
from datetime import date
from types import SimpleNamespace
from typing import Self
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx2 import Response as Reply
from sqlalchemy.dialects import postgresql

from tests.shared.paths import path
from tuttitrip.demo import api as demo_api
from tuttitrip.demo.api import get_client_factory, get_rate_limiter
from tuttitrip.demo.logic.dataset import DEMO_TRIPS
from tuttitrip.demo.logic.rate_limit import RateLimiter
from tuttitrip.demo.logic.token import token_matches
from tuttitrip.demo.services import demo_service, seed_command
from tuttitrip.main import create_app
from tuttitrip.shared.config.settings import DemoSettings, Settings
from tuttitrip.trips import db as trips_db

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


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b'{"token": ""}',
        b'{"token": 5}',
        b'{"token": "' + b"x" * 513 + b'"}',
        b'["a"]',
        b"not json",
        b"",
        b"{" + b" " * 5000 + b"}",
    ],
    ids=[
        "no-token",
        "empty-token",
        "token-not-a-string",
        "token-too-long",
        "body-not-an-object",
        "not-json",
        "no-body",
        "oversized-body",
    ],
)
def test_every_bad_request_shape_is_the_same_404_without_calling_auth0(
    client: TestClient, stub: Auth0Stub, body: bytes
) -> None:
    response = client.post(path("demo_login"), content=body)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert response.headers["cache-control"] == "no-store"
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
    assert _post(web, "").status_code == 404
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
    assert "reason=status_403" in caplog.text
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
    assert [_post(web, "x").status_code for _ in range(3)] == [404, 404, 429]
    limited = web.post(path("demo_login"), content=b"garbage")  # before parsing
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    assert limited.headers["cache-control"] == "no-store"
    # A header the client adds does not buy a new budget.
    spoofed = web.post(
        path("demo_login"),
        json={"token": TOKEN},
        headers={"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "198.51.100.1"},
    )
    assert spoofed.status_code == 429
    assert stub.requests == []
    get_rate_limiter.cache_clear()


def test_the_limiter_caps_the_keys_it_remembers() -> None:
    limiter = RateLimiter(1, max_keys=3)
    for key in "abcd":
        assert limiter.allow(key)
    assert len(limiter._hits) == 3  # ruff: ignore[private-member-access]
    assert limiter.allow("a")  # evicted earlier, so it starts fresh


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


def _trip_services(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Replace the other domains' services with recording doubles."""
    profile = SimpleNamespace(id=uuid.uuid4())
    fakes = SimpleNamespace(
        trips=SimpleNamespace(
            delete_trips_owned_by=AsyncMock(return_value=2),
            get_membership=AsyncMock(return_value=MagicMock()),
            create_trip=AsyncMock(return_value=SimpleNamespace(id="new")),
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


def test_the_reset_replaces_the_accounts_own_trips_with_the_sample_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fakes = _trip_services(monkeypatch)
    created = asyncio.run(
        demo_service.reset_demo_account(MagicMock(), "auth0|demo", date(2026, 10, 4))
    )
    assert created == len(DEMO_TRIPS) == fakes.trips.create_trip.await_count
    fakes.trips.delete_trips_owned_by.assert_awaited_once()
    assert fakes.trips.delete_trips_owned_by.await_args.args[1] == "auth0|demo"
    people = sum(len(t.people) for t in DEMO_TRIPS)
    assert fakes.preferences.replace_preferences.await_count == people
    assert fakes.profiles.create_profile.await_count == people - len(DEMO_TRIPS)
    assert fakes.profiles.set_weights.await_count == 1
    owners = {call.args[1] for call in fakes.trips.create_trip.await_args_list}
    assert owners == {"auth0|demo"}


def test_deleting_trips_is_scoped_to_the_owner_sub() -> None:
    session = MagicMock()
    result = MagicMock()
    result.all.return_value = [object()]
    session.execute = AsyncMock(return_value=result)
    assert asyncio.run(trips_db.delete_trips_owned_by(session, "auth0|demo")) == 1
    call = session.execute.await_args
    assert call is not None
    statement = call.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "WHERE trips.owner_sub = %(owner_sub_1)s" in str(compiled)
    assert compiled.params == {"owner_sub_1": "auth0|demo"}


class FakeConnection:
    """Records the transaction around the reset."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def begin(self) -> FakeTransaction:
        return FakeTransaction(self.events)

    async def execute(self, statement: object, params: object = None) -> None:
        self.events.append(f"execute {statement} {params}")


class FakeTransaction:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def __aenter__(self) -> None:
        self.events.append("begin")

    async def __aexit__(
        self, exc_type: type[BaseException] | None, *rest: object
    ) -> None:
        self.events.append("rollback" if exc_type else "commit")


def _plain_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MagicMock()
    session.close = AsyncMock()
    monkeypatch.setattr(demo_service, "AsyncSession", MagicMock(return_value=session))


def _engine(connection: FakeConnection) -> MagicMock:
    engine = MagicMock()
    engine.connect.return_value = connection
    return engine


def test_the_reset_runs_in_one_locked_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    _plain_session(monkeypatch)
    monkeypatch.setattr(demo_service, "reset_demo_account", AsyncMock(return_value=4))
    assert asyncio.run(demo_service.run_reset(_engine(connection), "auth0|demo")) == 4
    assert connection.events[0] == "begin"
    assert "pg_advisory_xact_lock" in connection.events[1]
    assert connection.events[-1] == "commit"


def test_a_failure_in_the_middle_of_the_seed_rolls_everything_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fakes = _trip_services(monkeypatch)
    fakes.trips.create_trip.side_effect = [
        SimpleNamespace(id="a"),
        RuntimeError("boom"),
    ]
    connection = FakeConnection()
    _plain_session(monkeypatch)
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(demo_service.run_reset(_engine(connection), "auth0|demo"))
    assert connection.events[-1] == "rollback"
    assert "commit" not in connection.events


def test_the_command_does_nothing_when_the_demo_is_off() -> None:
    assert asyncio.run(seed_command.run(Settings(demo=_demo(token_sha256="")))) == 0


def test_the_demo_sub_comes_from_the_demo_login_only() -> None:
    assert "user_sub" not in DemoSettings.model_fields


@pytest.mark.parametrize("digest", ["abc", "z" * 64, SHA + "0"])
def test_a_malformed_digest_fails_at_startup(digest: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        _demo(token_sha256=digest)


@pytest.mark.parametrize("missing", ["username", "password", "client_id"])
def test_an_enabled_demo_needs_the_whole_account(missing: str) -> None:
    with pytest.raises(ValueError, match=missing.upper()):
        _demo(**{missing: ""})


def test_the_advisory_lock_key_fits_a_bigint() -> None:
    assert 0 < demo_service.RESET_LOCK_KEY < 2**63
