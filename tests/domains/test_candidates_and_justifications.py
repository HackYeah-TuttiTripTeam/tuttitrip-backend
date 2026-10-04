"""Candidates for a new city (#72) and verdict justifications (#73): units."""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.shared.fakes import FakeJobQueue, authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.places.candidates.logic.slug import slugify
from tuttitrip.places.candidates.schemas import CandidatesState, CandidatesStatus
from tuttitrip.places.candidates.services import candidate_service
from tuttitrip.planning.plans.logic.justification import template_justification
from tuttitrip.planning.plans.logic.sample_plan import sample_plan
from tuttitrip.planning.plans.schemas import PlanErrorCode, VerdictKind
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.plans.services.plan_service import CatalogMissingError
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.jobs.api import get_job_queue
from tuttitrip.shared.jobs.contracts import Locale
from tuttitrip.shared.jobs.schemas import JobAccepted
from tuttitrip.shared.jobs.services.job_queue import JobQueueUnavailableError
from tuttitrip.shared.jobs.services.worker_liveness import WorkerUnavailableError
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

BOB = AuthenticatedUser(sub="auth0|bob")
TRIP = uuid.UUID("00000000-0000-4000-8000-000000000001")


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Gdańsk, Polska", "gdansk-polska"),
        ("  Łódź ", "lodz"),
        ("New  York", "new-york"),
        ("???", ""),
    ],
)
def test_slug_follows_the_contract_rule(name: str, slug: str) -> None:
    assert slugify(name) == slug


def _client(
    monkeypatch: pytest.MonkeyPatch,
    queue: FakeJobQueue,
    grants: tuple[Grant, ...] | None = None,
) -> TestClient:
    monkeypatch.setattr(
        trip_service,
        "get_membership",
        AsyncMock(
            return_value=TripMembership(trip_id=TRIP, sub=BOB.sub, role=TripRole.HOST)
        ),
    )
    app = create_app()
    if grants is None:
        authorize(app, BOB)
    else:
        authorize(app, BOB, grants)
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[get_job_queue] = lambda: queue
    return TestClient(app)


def test_a_new_city_gets_202_with_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = FakeJobQueue()
    monkeypatch.setattr(
        candidate_service,
        "request_candidates",
        AsyncMock(return_value=JobAccepted(workflow_id="fetch_place_candidates-x-1")),
    )
    with _client(monkeypatch, queue) as client:
        response = client.post(path("request_candidates", trip_id=TRIP))
    assert response.status_code == 202
    assert response.json() == {"workflow_id": "fetch_place_candidates-x-1"}


def test_a_city_with_places_is_200_ready_and_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = CandidatesStatus(
        city_slug="krakow", place_count=40, state=CandidatesState.READY
    )
    monkeypatch.setattr(
        candidate_service, "request_candidates", AsyncMock(return_value=ready)
    )
    with _client(monkeypatch, FakeJobQueue()) as client:
        response = client.post(path("request_candidates", trip_id=TRIP))
    assert response.status_code == 200
    assert response.json()["state"] == "ready"


@pytest.mark.parametrize(
    "error",
    [
        WorkerUnavailableError("No tuttitrip-worker has reported"),
        JobQueueUnavailableError("x"),
    ],
)
def test_without_a_worker_the_answer_is_503(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.setattr(
        candidate_service, "request_candidates", AsyncMock(side_effect=error)
    )
    with _client(monkeypatch, FakeJobQueue()) as client:
        response = client.post(path("request_candidates", trip_id=TRIP))
    assert response.status_code == 503


def test_asking_needs_the_candidates_permission_and_reading_the_catalog_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = (Grant(Feature.PLACES_CATALOG, Access.READ),)
    with _client(monkeypatch, FakeJobQueue(), reader) as client:
        assert client.post(path("request_candidates", trip_id=TRIP)).status_code == 403
    monkeypatch.setattr(
        candidate_service,
        "candidates_status",
        AsyncMock(
            return_value=CandidatesStatus(
                city_slug="krakow", place_count=0, state=CandidatesState.EMPTY
            )
        ),
    )
    with _client(monkeypatch, FakeJobQueue(), reader) as client:
        assert client.get(path("candidates_status", trip_id=TRIP)).status_code == 200


# --- 409 catalog_missing ----------------------------------------------------------


def test_a_plan_for_a_city_without_places_is_409_with_the_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plan_service,
        "generate_plan",
        AsyncMock(side_effect=CatalogMissingError("sopot")),
    )
    monkeypatch.setattr(
        candidate_service,
        "job_for_missing_catalog",
        AsyncMock(return_value="fetch_place_candidates-sopot-9"),
    )
    with _client(monkeypatch, FakeJobQueue()) as client:
        response = client.post(path("create_plan", trip_id=TRIP))
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == PlanErrorCode.CATALOG_MISSING
    assert detail["city_slug"] == "sopot"
    assert detail["job_id"] == "fetch_place_candidates-sopot-9"


# --- justifications ---------------------------------------------------------------


@pytest.mark.parametrize("locale", ["pl", "en"])
def test_every_verdict_has_a_template_in_both_languages(locale: Locale) -> None:
    for kind in VerdictKind:
        text = template_justification(kind, 3, 1, ["veto", "unknown"], locale)
        assert text
    fits = template_justification(VerdictKind.FITS, 3, 1, [], locale)
    assert "3" in fits
    assert "1" in fits


def test_a_skip_names_its_reasons_and_ignores_unknown_codes() -> None:
    text = template_justification(VerdictKind.SKIP, 0, 2, ["veto", "bogus"], "en")
    assert "vetoed" in text
    assert "bogus" not in text


def test_sample_plan_verdicts_carry_the_new_fields() -> None:
    plan = sample_plan(TRIP)
    for verdict in plan.verdicts or []:
        assert verdict.justification
        assert verdict.justification_source in {"template", "model"}
