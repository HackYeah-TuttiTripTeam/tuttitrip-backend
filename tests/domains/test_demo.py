"""Jury one-link login: token check, Auth0 grant, rate limit, sample data."""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Self
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx2 import Response as Reply
from sqlalchemy.dialects import postgresql

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.demo import api as demo_api
from tuttitrip.demo.api import get_client_factory, get_rate_limiter
from tuttitrip.demo.logic.dataset import DEMO_ACCOUNT_NAME, DEMO_TRIPS
from tuttitrip.demo.logic.token import secret_matches, token_matches
from tuttitrip.demo.services import demo_service, reset_service, seed_command
from tuttitrip.main import create_app
from tuttitrip.places.schemas import Amenity
from tuttitrip.planning.plans.services.plan_service import PlanInputError
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.config.settings import Auth0Settings, DemoSettings, Settings
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.shared.rate_limit.limiter import RateLimiter
from tuttitrip.trips import db as trips_db

ROOT = Path(__file__).resolve().parents[2]
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
    assert warszawa.name == "Warszawa z rodziną"
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
        documents=SimpleNamespace(create_document=AsyncMock()),
        plans=SimpleNamespace(
            generate_plan=AsyncMock(
                return_value=(SimpleNamespace(plan_hash="a1b2c3d4e5f6"), True)
            )
        ),
    )
    monkeypatch.setattr(demo_service, "trip_service", fakes.trips)
    monkeypatch.setattr(demo_service, "profile_service", fakes.profiles)
    monkeypatch.setattr(demo_service, "preference_service", fakes.preferences)
    monkeypatch.setattr(demo_service, "requirements_service", fakes.requirements)
    monkeypatch.setattr(demo_service, "place_service", fakes.places)
    monkeypatch.setattr(demo_service, "document_service", fakes.documents)
    monkeypatch.setattr(demo_service, "plan_service", fakes.plans)
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
    monkeypatch.setattr(
        demo_service, "reset_demo_account", AsyncMock(return_value=len(DEMO_TRIPS))
    )
    reset = asyncio.run(demo_service.run_reset(_engine(connection), "auth0|demo"))
    assert reset == len(DEMO_TRIPS)
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


# --- internal reset endpoint (the worker's daily schedule) ---------------------

RESET_SECRET = "reset-secret-for-tests-only"


@pytest.fixture
def internal(stub: Auth0Stub, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    settings = Settings(demo=_demo(reset_secret=RESET_SECRET))
    monkeypatch.setattr(demo_api, "get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_client_factory] = stub.factory
    return TestClient(app)


class ResetSpy:
    """Replaces the Auth0 sub lookup and the data reset."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.reset = AsyncMock(return_value=len(DEMO_TRIPS))
        monkeypatch.setattr(
            reset_service, "demo_sub", AsyncMock(return_value="auth0|demo")
        )
        monkeypatch.setattr(reset_service, "get_engine", object)
        monkeypatch.setattr(demo_service, "run_reset", self.reset)

    @property
    def subs(self) -> list[str]:
        return [call.args[1] for call in self.reset.await_args_list]


def _reset(client: TestClient, secret: str | None = RESET_SECRET) -> Reply:
    headers = {} if secret is None else {"Authorization": f"Bearer {secret}"}
    return client.post(path("reset_demo"), headers=headers)


def test_the_internal_reset_runs_the_same_reset_as_the_deploy(
    internal: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = ResetSpy(monkeypatch)
    response = _reset(internal)
    assert response.status_code == 200
    assert response.json() == {"status": "reset", "trips": len(DEMO_TRIPS)}
    assert response.headers["cache-control"] == "no-store"
    assert spy.subs == ["auth0|demo"]


@pytest.mark.parametrize("secret", [None, "", "wrong", RESET_SECRET + "x"])
def test_a_wrong_or_missing_secret_is_a_plain_404(
    internal: TestClient,
    stub: Auth0Stub,
    monkeypatch: pytest.MonkeyPatch,
    secret: str | None,
) -> None:
    spy = ResetSpy(monkeypatch)
    response = _reset(internal, secret)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert response.headers["cache-control"] == "no-store"
    assert spy.subs == []
    assert stub.requests == []


@pytest.mark.parametrize("scheme", ["Basic", "Token"])
def test_only_the_bearer_scheme_is_accepted(
    internal: TestClient, monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    spy = ResetSpy(monkeypatch)
    response = internal.post(
        path("reset_demo"), headers={"Authorization": f"{scheme} {RESET_SECRET}"}
    )
    assert response.status_code == 404
    assert spy.subs == []


def test_without_a_configured_secret_the_endpoint_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(demo=_demo())
    monkeypatch.setattr(demo_api, "get_settings", lambda: settings)
    spy = ResetSpy(monkeypatch)
    web = TestClient(create_app())
    assert _reset(web, "").status_code == 404
    assert _reset(web, None).status_code == 404
    assert _reset(web, "anything").status_code == 404
    assert spy.subs == []


def test_a_switched_off_demo_answers_disabled_and_touches_nothing(
    stub: Auth0Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(demo=_demo(token_sha256="", reset_secret=RESET_SECRET))
    monkeypatch.setattr(demo_api, "get_settings", lambda: settings)
    spy = ResetSpy(monkeypatch)
    response = _reset(TestClient(create_app()))
    assert response.status_code == 200
    assert response.json() == {"status": "disabled"}
    assert spy.subs == []
    assert stub.requests == []


def test_an_auth0_failure_during_the_reset_is_a_502_without_secrets(
    internal: TestClient, stub: Auth0Stub, caplog: pytest.LogCaptureFixture
) -> None:
    stub.status = 403
    with caplog.at_level("DEBUG"):
        response = _reset(internal)
    assert response.status_code == 502
    assert "reason=status_403" in caplog.text
    for secret in (RESET_SECRET, ACCOUNT_PASSWORD, "demo@example.test"):
        assert secret not in caplog.text + response.text


def test_the_internal_reset_is_not_in_the_public_openapi() -> None:
    assert path("reset_demo") == "/api/v1/internal/demo/reset"
    assert path("reset_demo") not in create_app().openapi()["paths"]


def test_the_gateway_never_forwards_internal_paths() -> None:
    conf = (ROOT / "deploy/gateway/nginx.conf").read_text()
    block = "location /api/v1/internal/ { return 404; }"
    assert block in conf
    assert conf.index(block) < conf.index("location / {")


def test_the_deploy_gives_api_and_worker_the_same_generated_secret() -> None:
    deploy = (ROOT / "deploy/deploy.sh").read_text()
    assert "WORKER_DB_PASSWORD DEMO_RESET_SECRET; do" in deploy
    line = "printf 'TUTTITRIP_DEMO__RESET_SECRET=%s\\n' \"$demo_reset_secret\""
    assert deploy.count(line) == 2  # the API env file and the worker env file


@pytest.mark.parametrize("secret", ["short", "x" * 23])
def test_a_short_reset_secret_fails_at_startup(secret: str) -> None:
    with pytest.raises(ValueError, match="at least 24"):
        _demo(reset_secret=secret)


def test_an_empty_or_long_reset_secret_is_accepted() -> None:
    assert not _demo(reset_secret="").reset_secret.get_secret_value()
    assert _demo(reset_secret="x" * 24)


def test_the_deploy_derives_the_secret_per_environment() -> None:
    deploy = (ROOT / "deploy/deploy.sh").read_text()
    assert '\'%s:%s\' "$DEMO_RESET_SECRET" "$env" | sha256sum' in deploy
    assert (
        '=%s\\n\' "$DEMO_RESET_SECRET"' not in deploy
    )  # the raw secret is never written


def test_the_secret_comparison_is_exact_and_empty_never_matches() -> None:
    assert secret_matches("abc", "abc")
    assert not secret_matches("abd", "abc")
    assert not secret_matches("", "")
    assert not secret_matches("x", "")


# --- the demo account's Auth0 name is restored by the reset ---------------------


class ManagementStub:
    """Fake Auth0 for the reset's name restore: M2M token and user update."""

    def __init__(self, update_status: int = 200) -> None:
        self.update_status = update_status
        self.updates: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(
                200, json={"access_token": "mgmt-jwt", "expires_in": 86400}
            )
        self.updates.append(request)
        return httpx.Response(self.update_status, json={})

    def factory(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def _with_management() -> Settings:
    auth0 = Auth0Settings.model_validate(
        {
            "management_client_id": "cid",
            "management_client_secret": "m2m-secret-for-tests",
        }
    )
    return Settings(demo=_demo(), auth0=auth0)


def test_the_reset_restores_the_demo_account_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = ResetSpy(monkeypatch)
    auth0 = ManagementStub()
    trips = asyncio.run(reset_service.reset_demo(_with_management(), auth0.factory))
    assert trips == len(DEMO_TRIPS)
    assert spy.subs == ["auth0|demo"]
    (sent,) = auth0.updates
    assert sent.method == "PATCH"
    assert sent.url.raw_path == b"/api/v2/users/auth0%7Cdemo"
    assert json.loads(sent.content) == {"name": DEMO_ACCOUNT_NAME}


def test_a_failed_name_restore_does_not_break_the_reset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ResetSpy(monkeypatch)
    auth0 = ManagementStub(update_status=403)
    with caplog.at_level("WARNING"):
        trips = asyncio.run(reset_service.reset_demo(_with_management(), auth0.factory))
    assert trips == len(DEMO_TRIPS)
    assert "Demo account name not restored: reason=status_403" in caplog.text
    assert "m2m-secret-for-tests" not in caplog.text


def test_without_management_credentials_the_name_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ResetSpy(monkeypatch)
    auth0 = ManagementStub()
    trips = asyncio.run(reset_service.reset_demo(Settings(demo=_demo()), auth0.factory))
    assert trips == len(DEMO_TRIPS)
    assert auth0.updates == []


def test_the_family_trip_has_the_scene_data() -> None:
    family = DEMO_TRIPS[-1]
    assert family.plan
    assert Amenity.POOL in family.hard_amenities  # "a pool in every lodging"
    assert family.chatbot_days
    assert family.offer_text is not None
    assert family.budget_total == (1300, 1700)
    assert family.flex_pct == 10


def test_the_tight_budget_variant_is_the_same_family_at_900_to_1100() -> None:
    family, tight = DEMO_TRIPS[-1], DEMO_TRIPS[-2]
    assert tight.budget_total == (900, 1100)
    assert tight.people == family.people
    assert tight.hard_amenities == family.hard_amenities
    assert tight.plan
    assert tight.flex_pct == family.flex_pct
    assert tight.chatbot_days == ()  # the chatbot plan belongs to the main trip only


def test_the_reset_stores_the_pasted_plan_and_offer_and_computes_the_plans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fakes = _trip_services(monkeypatch)
    asyncio.run(
        demo_service.reset_demo_account(MagicMock(), "auth0|demo", date(2026, 10, 4))
    )
    documents = fakes.documents.create_document.await_args_list
    assert sorted(call.args[2].kind.value for call in documents) == ["offer", "plan"]
    assert fakes.plans.generate_plan.await_count == sum(1 for t in DEMO_TRIPS if t.plan)
    assert fakes.plans.generate_plan.await_count == 2


def test_a_trip_without_a_catalog_is_still_seeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fakes = _trip_services(monkeypatch)
    fakes.plans.generate_plan.side_effect = PlanInputError("no city")
    created = asyncio.run(
        demo_service.reset_demo_account(MagicMock(), "auth0|demo", date(2026, 10, 4))
    )
    assert created == len(DEMO_TRIPS)
    assert fakes.documents.create_document.await_count == 2


def test_the_admin_reset_needs_admin_demo_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ResetSpy(monkeypatch)
    user_grants = [Grant(Feature.TRIPS_CORE, Access.WRITE)]
    admin = create_app()
    authorize(admin, AuthenticatedUser(sub="auth0|user"), user_grants)
    assert TestClient(admin).post(path("admin_reset_demo")).status_code == 403


def test_a_superadmin_resets_the_demo_by_hand(
    stub: Auth0Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(demo=_demo())
    monkeypatch.setattr(demo_api, "get_settings", lambda: settings)
    spy = ResetSpy(monkeypatch)
    app = create_app()
    authorize(app, AuthenticatedUser(sub="auth0|root"))
    app.dependency_overrides[get_client_factory] = stub.factory
    response = TestClient(app).post(path("admin_reset_demo"))
    assert response.status_code == 200
    assert response.json() == {"status": "reset", "trips": len(DEMO_TRIPS)}
    assert spy.subs == ["auth0|demo"]
