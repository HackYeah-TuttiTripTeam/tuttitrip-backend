"""Jobs API, contract snapshot and worker liveness, without a database."""

import asyncio
import json
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from dbos import WorkflowStatus
from fastapi.testclient import TestClient

from tests.shared.fakes import FakeJobQueue, authorize
from tuttitrip.main import create_app
from tuttitrip.planning.services import plan_job_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.jobs import contracts
from tuttitrip.shared.jobs.api import get_job_queue
from tuttitrip.shared.jobs.contracts import (
    CONTRACT_VERSION,
    GenerateTripPlanInput,
    PingInput,
    Workflow,
)
from tuttitrip.shared.jobs.models import WorkerHeartbeat
from tuttitrip.shared.jobs.services import worker_liveness
from tuttitrip.shared.jobs.services.job_queue import (
    TIMEOUT_SECONDS,
    job_state,
    workflow_id_for,
)
from tuttitrip.shared.jobs.services.worker_liveness import (
    WorkerUnavailableError,
    get_worker_liveness,
)

SNAPSHOT = Path(__file__).resolve().parents[2] / "contracts" / "jobs.schema.json"
ALICE = AuthenticatedUser(sub="auth0|alice")


@pytest.fixture
def queue() -> FakeJobQueue:
    return FakeJobQueue()


@pytest.fixture
def client(queue: FakeJobQueue) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_job_queue] = lambda: queue
    authorize(app, ALICE)
    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app) as test_client:
        yield test_client


# --- contract --------------------------------------------------------------


def test_committed_contract_matches_the_mirror() -> None:
    # Regenerate with:
    # uv run python -c "from tuttitrip.shared.jobs.contracts import contract_json;
    #   print(contract_json(), end='')" > contracts/jobs.schema.json
    assert SNAPSHOT.read_text(encoding="utf-8") == contracts.contract_json()


def test_contract_lists_every_workflow_with_its_queue() -> None:
    document = json.loads(contracts.contract_json())
    assert document["contract_version"] == CONTRACT_VERSION
    assert set(document["workflows"]) == {w.value for w in Workflow}
    assert document["workflows"]["ping"]["queue"] == "default"
    assert document["queues"] == ["default", "local_llm", "openrouter"]


def test_payloads_carry_the_contract_version() -> None:
    payload = PingInput(message="x").model_dump(mode="json")
    assert payload["contract_version"] == CONTRACT_VERSION


def test_workflow_ids_are_deterministic() -> None:
    trip = uuid.uuid4()
    first = GenerateTripPlanInput(trip_id=trip, request="Gdańsk, 3 dni")
    same = GenerateTripPlanInput(trip_id=trip, request="Gdańsk, 3 dni")
    other = GenerateTripPlanInput(trip_id=trip, request="Sopot, 2 dni")
    key = str(trip)
    wf = Workflow.GENERATE_TRIP_PLAN
    assert workflow_id_for(wf, key, first) == workflow_id_for(wf, key, same)
    assert workflow_id_for(wf, key, first) != workflow_id_for(wf, key, other)
    assert workflow_id_for(wf, key, first).startswith(f"generate_trip_plan-{trip}-")


# --- endpoints -------------------------------------------------------------


def test_ping_is_public_and_visible_only_as_ping(
    client: TestClient, queue: FakeJobQueue
) -> None:
    accepted = client.post("/api/v1/jobs/ping")
    assert accepted.status_code == 202
    workflow_id = accepted.json()["workflow_id"]
    assert client.get(f"/api/v1/jobs/ping/{workflow_id}").json()["status"] == "ENQUEUED"
    assert queue.jobs[workflow_id].owner == "smoke-test"


def test_jobs_are_private_to_their_owner(
    client: TestClient, queue: FakeJobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan_job_service.trip_service, "get_membership", AsyncMock())
    monkeypatch.setattr(plan_job_service, "ensure_worker_available", AsyncMock())
    body = {"trip_id": str(uuid.uuid4()), "request": "Gdańsk, 3 dni"}

    first = client.post("/api/v1/planning/jobs", json=body)
    again = client.post("/api/v1/planning/jobs", json=body)
    assert first.status_code == 202
    workflow_id = first.json()["workflow_id"]
    assert again.json()["workflow_id"] == workflow_id  # idempotent
    assert len(queue.jobs) == 1
    assert queue.queues[workflow_id] == "openrouter"

    assert client.get(f"/api/v1/jobs/{workflow_id}").json()["owner"] == ALICE.sub
    assert client.get(f"/api/v1/jobs/ping/{workflow_id}").status_code == 404

    queue.jobs[workflow_id] = queue.jobs[workflow_id].model_copy(
        update={"owner": "auth0|mallory"}
    )
    assert client.get(f"/api/v1/jobs/{workflow_id}").status_code == 404


def test_cancel(client: TestClient, queue: FakeJobQueue) -> None:
    workflow_id = client.post("/api/v1/jobs/ping").json()["workflow_id"]
    queue.jobs[workflow_id] = queue.jobs[workflow_id].model_copy(
        update={"owner": ALICE.sub}
    )
    response = client.post(f"/api/v1/jobs/{workflow_id}/cancel")
    assert response.json()["status"] == "CANCELLED"


def test_unknown_job_is_404(client: TestClient) -> None:
    assert client.get("/api/v1/jobs/nope").status_code == 404


def test_plan_job_refused_without_worker(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan_job_service.trip_service, "get_membership", AsyncMock())
    monkeypatch.setattr(
        plan_job_service,
        "ensure_worker_available",
        AsyncMock(side_effect=WorkerUnavailableError("no worker")),
    )
    body = {"trip_id": str(uuid.uuid4()), "request": "x"}
    response = client.post("/api/v1/planning/jobs", json=body)
    assert response.status_code == 503
    assert response.json()["detail"] == "no worker"


# --- liveness --------------------------------------------------------------


def _beat(
    age_seconds: int, low: int = 1, high: int = CONTRACT_VERSION
) -> WorkerHeartbeat:
    return WorkerHeartbeat(
        worker_id="w1",
        env="local",
        contract_version=high,
        min_contract_version=low,
        app_version="local",
        last_seen=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )


@pytest.mark.parametrize(
    ("beat", "expected"),
    [
        (None, "missing"),
        (_beat(5), "ok"),
        (_beat(300), "stale"),
        (_beat(3600), "missing"),
    ],
    ids=["none", "fresh", "stale", "old"],
)
def test_liveness_classification(
    monkeypatch: pytest.MonkeyPatch, beat: WorkerHeartbeat | None, expected: str
) -> None:
    monkeypatch.setattr(
        worker_liveness.db, "select_latest_heartbeat", AsyncMock(return_value=beat)
    )
    liveness = asyncio.run(get_worker_liveness(AsyncMock()))
    assert liveness.status == expected


def test_incompatible_worker_blocks_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    beat = _beat(5, low=CONTRACT_VERSION + 1, high=CONTRACT_VERSION + 1)
    monkeypatch.setattr(
        worker_liveness.db, "select_latest_heartbeat", AsyncMock(return_value=beat)
    )
    with pytest.raises(WorkerUnavailableError, match="contract versions"):
        asyncio.run(worker_liveness.ensure_worker_available(AsyncMock()))


def test_local_provider_goes_to_the_local_llm_queue(
    client: TestClient, queue: FakeJobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan_job_service.trip_service, "get_membership", AsyncMock())
    monkeypatch.setattr(plan_job_service, "ensure_worker_available", AsyncMock())
    body = {"trip_id": str(uuid.uuid4()), "request": "Gdańsk", "provider": "local"}
    workflow_id = client.post("/api/v1/planning/jobs", json=body).json()["workflow_id"]
    assert queue.queues[workflow_id] == "local_llm"
    assert queue.payloads[workflow_id].model_dump()["provider"] == "local"


def test_every_workflow_has_a_timeout() -> None:
    assert set(TIMEOUT_SECONDS) == set(Workflow)


# --- mirrored workflows ----------------------------------------------------


def test_mirror_has_the_same_workflows_as_the_worker() -> None:
    assert {w.value for w in Workflow} >= {
        "parse_pasted_plan",
        "extract_offer_evidence",
        "fetch_place_candidates",
        "write_justifications",
    }
    for workflow in Workflow:
        assert workflow in contracts.WORKFLOWS


def test_pasted_text_travels_by_id_not_in_the_payload() -> None:
    assert "text" not in contracts.ParsePastedPlanInput.model_fields
    assert "document_id" in contracts.ExtractOfferEvidenceInput.model_fields


def test_fetch_place_candidates_needs_exactly_one_city() -> None:
    contracts.FetchPlaceCandidatesInput(city_query="Gdańsk")
    contracts.FetchPlaceCandidatesInput(city_slug="gdansk")
    with pytest.raises(ValueError, match="exactly one"):
        contracts.FetchPlaceCandidatesInput()
    with pytest.raises(ValueError, match="exactly one"):
        contracts.FetchPlaceCandidatesInput(city_query="a", city_slug="a")


def test_offer_requirements_must_be_unique_known_keys() -> None:
    trip, document = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(ValueError, match="unique"):
        contracts.ExtractOfferEvidenceInput(
            trip_id=trip, document_id=document, requirement_keys=["a", "a"]
        )
    with pytest.raises(ValueError, match="requirement_keys"):
        contracts.ExtractOfferEvidenceInput(
            trip_id=trip,
            document_id=document,
            requirement_keys=["a"],
            requirements=[contracts.RequirementLabel(key="b", label="B")],
        )


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Gdańsk", "gdansk"),
        ("Gdańsk, Polska", "gdansk-polska"),
        ("Łódź", "lodz"),
        ("  Kraków  --  Stare Miasto ", "krakow-stare-miasto"),
        ("São Paulo", "sao-paulo"),
    ],
)
def test_city_slug_follows_the_worker_rule(name: str, slug: str) -> None:
    assert contracts.city_slug(name) == slug
    assert re.fullmatch(contracts.SLUG_PATTERN, slug)


class _WorkerError(Exception):
    """Stands in for DBOS's ``PortableWorkflowError`` (message and ``code``)."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("not_implemented", "Jeszcze niedostępne"),
        ("invalid_payload", "invalid ParsePastedPlanInput"),
    ],
)
def test_worker_error_codes_reach_the_job_state(code: str, message: str) -> None:
    status = SimpleNamespace(
        workflow_id="w1",
        name="parse_pasted_plan",
        status="ERROR",
        authenticated_user="auth0|alice",
        output=None,
        error=_WorkerError("invalid ParsePastedPlanInput", code),
    )
    state = job_state(cast("WorkflowStatus", status), None)
    assert state.error_code == code
    assert state.error == message
