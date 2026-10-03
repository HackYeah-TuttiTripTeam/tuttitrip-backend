"""/health reports the database state; /health/live does not touch it."""

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tuttitrip.main import create_app
from tuttitrip.shared.health.services import health_check
from tuttitrip.shared.jobs.contracts import CONTRACT_VERSION
from tuttitrip.shared.jobs.schemas import WorkerLiveness


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(
        health_check,
        "worker_liveness",
        AsyncMock(return_value=WorkerLiveness(status="ok", backend_contract_version=1)),
    )
    with TestClient(create_app()) as test_client:
        yield test_client


def test_health_ok_when_database_answers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_check, "ping", AsyncMock(return_value=True))
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_health_503_when_database_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_check, "ping", AsyncMock(return_value=False))
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "database": "unavailable",
        "environment": "local",
        "worker": "missing",
        "contract_version": CONTRACT_VERSION,
        "worker_contract_version": None,
    }


def test_health_degraded_when_worker_contract_is_incompatible(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_check, "ping", AsyncMock(return_value=True))
    liveness = WorkerLiveness(
        status="ok",
        backend_contract_version=CONTRACT_VERSION,
        worker_contract_version=CONTRACT_VERSION + 2,
        worker_min_contract_version=CONTRACT_VERSION + 1,
        compatible=False,
    )
    monkeypatch.setattr(
        health_check, "worker_liveness", AsyncMock(return_value=liveness)
    )
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json()["worker_contract_version"] == CONTRACT_VERSION + 2


def test_live_does_not_need_the_database(client: TestClient) -> None:
    assert client.get("/api/v1/health/live").json() == {"status": "ok"}


def test_openapi_schema_is_public(client: TestClient) -> None:
    # The frontend generates its TypeScript client from this at build time.
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert "/api/v1/trips" in response.json()["paths"]


ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "https://tuttitrip.gburek.app",
    "https://tuttitrip-develop.gburek.app",
    "https://tuttitrip-preview-feature-x.acme.workers.dev",
]
BLOCKED_ORIGINS = [
    "https://evil.example.com",
    "https://tuttitrip-preview-x.acme.workers.dev.evil.com",
    "https://tuttitrip-api.gburek.app",
]


@pytest.mark.parametrize("origin", ALLOWED_ORIGINS)
def test_cors_allows_our_frontends(client: TestClient, origin: str) -> None:
    response = client.get("/api/v1/health/live", headers={"Origin": origin})
    assert response.headers.get("access-control-allow-origin") == origin


@pytest.mark.parametrize("origin", BLOCKED_ORIGINS)
def test_cors_blocks_other_origins(client: TestClient, origin: str) -> None:
    response = client.get("/api/v1/health/live", headers={"Origin": origin})
    assert "access-control-allow-origin" not in response.headers
