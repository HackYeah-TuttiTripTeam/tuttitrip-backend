"""/health reports the database state; /health/live does not touch it."""

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tuttitrip.main import create_app
from tuttitrip.shared.health.services import health_check


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def test_health_ok_when_database_answers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_check, "ping", AsyncMock(return_value=True))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_health_503_when_database_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_check, "ping", AsyncMock(return_value=False))
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "database": "unavailable",
        "environment": "local",
    }


def test_live_does_not_need_the_database(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}


def test_openapi_schema_is_public(client: TestClient) -> None:
    # The frontend generates its TypeScript client from this at build time.
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "/trips" in response.json()["paths"]
