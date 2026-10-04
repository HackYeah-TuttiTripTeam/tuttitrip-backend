"""Text expense entry: name matching and the draft endpoints (no model calls)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.shared.fakes import FakeJobQueue, authorize
from tests.shared.paths import path
from tuttitrip.expenses.logic.names import match_name, normalize, resolve_people
from tuttitrip.expenses.services import expense_draft_service
from tuttitrip.main import create_app
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.jobs.api import get_job_queue
from tuttitrip.shared.jobs.contracts import ParseExpenseTextInput, Queue, Workflow
from tuttitrip.shared.jobs.schemas import JobState
from tuttitrip.shared.jobs.services.worker_liveness import WorkerUnavailableError
from tuttitrip.trips.schemas import TripMembership, TripRead, TripRole
from tuttitrip.trips.services import trip_service

TRIP = uuid.uuid4()
KASIA, ANIA, TOMEK = (uuid.UUID(int=n) for n in (1, 2, 3))
PEOPLE = {KASIA: "Kasia", ANIA: "Ania", TOMEK: "Tomek"}
ME = AuthenticatedUser(sub="auth0|tomek")


def test_normalize_drops_diacritics_and_case() -> None:
    assert normalize(" Łukasz ") == "lukasz"
    assert normalize("Kasię") == "kasie"


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("Kasia", KASIA),
        ("kasię", KASIA),
        ("Ani", ANIA),
        ("Anię", ANIA),
        ("Tomka", TOMEK),
    ],
)
def test_names_match_in_declined_forms(written: str, expected: uuid.UUID) -> None:
    assert match_name(written, PEOPLE).profile_id == expected


def test_unknown_and_ambiguous_names_do_not_resolve() -> None:
    assert match_name("Zosia", PEOPLE).candidates == ()
    twins = {**PEOPLE, uuid.UUID(int=4): "Anita"}
    ambiguous = match_name("Ani", twins)
    assert ambiguous.profile_id is None
    assert set(ambiguous.candidates) == {ANIA, uuid.UUID(int=4)}
    assert match_name("Ania", twins).profile_id == ANIA  # an exact name wins


def test_the_example_sentence_becomes_payer_kasia_without_ania() -> None:
    people = resolve_people(
        payer_name="Kasia",
        included=[],
        excluded=["Ani"],
        people=PEOPLE,
        caller=TOMEK,
        confidence=0.9,
    )
    assert people.payer == KASIA
    assert people.participants == [KASIA, TOMEK]
    assert people.issues == []


def test_missing_things_are_issues() -> None:
    unknown = resolve_people(
        payer_name="Zosia",
        included=[],
        excluded=[],
        people=PEOPLE,
        caller=None,
        confidence=0.3,
    )
    assert [i.code for i in unknown.issues] == ["name_not_on_trip", "low_confidence"]
    nobody = resolve_people(
        payer_name=None,
        included=[],
        excluded=["Kasia", "Ania", "Tomek"],
        people=PEOPLE,
        caller=None,
        confidence=None,
    )
    assert [i.code for i in nobody.issues] == ["payer_missing", "participants_empty"]
    assert nobody.participants == [KASIA, ANIA, TOMEK]


def _session() -> AsyncMock:
    return AsyncMock()


def _client(
    monkeypatch: pytest.MonkeyPatch, queue: FakeJobQueue, *, worker_up: bool = True
) -> TestClient:
    def membership(_s: object, _t: uuid.UUID, sub: str, _r: TripRole) -> TripMembership:
        return TripMembership(trip_id=TRIP, sub=sub, role=TripRole.MEMBER)

    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(side_effect=membership)
    )
    monkeypatch.setattr(
        trip_service,
        "get_trip",
        AsyncMock(return_value=TripRead.model_construct(currency="PLN")),
    )
    profiles = [
        ProfileRead.model_construct(
            id=i, display_name=n, user_sub=ME.sub if i == TOMEK else None
        )
        for i, n in PEOPLE.items()
    ]
    monkeypatch.setattr(
        profile_service, "list_profiles", AsyncMock(return_value=profiles)
    )
    monkeypatch.setattr(
        expense_draft_service,
        "ensure_worker_available",
        AsyncMock(side_effect=None if worker_up else WorkerUnavailableError("down")),
    )
    app = create_app()
    authorize(app, ME)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_job_queue] = lambda: queue
    return TestClient(app)


SENTENCE = {"text": "obiad 142 zł, płaciła Kasia, bez Ani"}


def test_post_enqueues_on_the_local_queue_and_saves_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = FakeJobQueue()
    response = _client(monkeypatch, queue).post(
        path("start_expense_draft", trip_id=TRIP), json=SENTENCE
    )
    assert response.status_code == 202, response.text
    workflow_id = response.json()["workflow_id"]
    assert workflow_id.startswith(f"parse_expense_text-{TRIP}-")
    payload = queue.payloads[workflow_id]
    assert isinstance(payload, ParseExpenseTextInput)
    assert payload.text == SENTENCE["text"]
    assert queue.queues[workflow_id] is Queue.LOCAL_LLM


def test_no_worker_answers_503(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, FakeJobQueue(), worker_up=False)
    response = client.post(path("start_expense_draft", trip_id=TRIP), json=SENTENCE)
    assert response.status_code == 503


def _finish(queue: FakeJobQueue, workflow_id: str, output: dict[str, object]) -> None:
    queue.jobs[workflow_id] = JobState(
        workflow_id=workflow_id,
        workflow_name=Workflow.PARSE_EXPENSE_TEXT.value,
        status="SUCCESS",
        owner=ME.sub,
        output={"contract_version": 1, **output},
    )


def test_get_returns_the_draft_with_matched_people(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = FakeJobQueue()
    client = _client(monkeypatch, queue)
    workflow_id = client.post(
        path("start_expense_draft", trip_id=TRIP), json=SENTENCE
    ).json()["workflow_id"]
    url = path("get_expense_draft", trip_id=TRIP, workflow_id=workflow_id)
    assert client.get(url).json() == {"status": "pending", "draft": None}
    _finish(
        queue,
        workflow_id,
        {
            "amount_minor": 14200,
            "currency": "PLN",
            "description": "obiad",
            "payer_name": "Kasia",
            "excluded_names": ["Ani"],
            "confidence": 0.95,
        },
    )
    draft = client.get(url).json()["draft"]
    assert Decimal(draft["amount"]) == 142
    assert (draft["currency"], draft["payer_profile_id"]) == ("PLN", str(KASIA))
    assert draft["participants"] == [str(KASIA), str(TOMEK)]
    assert draft["needs_confirmation"] is False
    assert datetime.fromisoformat(draft["spent_on"]).date() <= datetime.now(UTC).date()


def test_unknown_name_needs_confirmation_and_failures_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = FakeJobQueue()
    client = _client(monkeypatch, queue)
    workflow_id = client.post(
        path("start_expense_draft", trip_id=TRIP), json=SENTENCE
    ).json()["workflow_id"]
    url = path("get_expense_draft", trip_id=TRIP, workflow_id=workflow_id)
    _finish(queue, workflow_id, {"amount_minor": 5000, "payer_name": "Zosia"})
    draft = client.get(url).json()["draft"]
    assert draft["needs_confirmation"] is True
    assert draft["issues"][0]["code"] == "name_not_on_trip"
    assert draft["issues"][0]["name"] == "Zosia"
    assert draft["currency"] == "PLN"  # the trip's, when the text has none
    queue.jobs[workflow_id] = queue.jobs[workflow_id].model_copy(
        update={"status": "ERROR", "output": None}
    )
    assert client.get(url).json() == {"status": "failed", "draft": None}


def test_other_trips_and_other_users_jobs_are_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = FakeJobQueue()
    client = _client(monkeypatch, queue)
    foreign = f"parse_expense_text-{uuid.uuid4()}-abc"
    queue.jobs[foreign] = JobState(
        workflow_id=foreign,
        workflow_name="parse_expense_text",
        status="SUCCESS",
        owner=ME.sub,
    )
    assert (
        client.get(
            path("get_expense_draft", trip_id=TRIP, workflow_id=foreign)
        ).status_code
        == 404
    )
    mine = f"parse_expense_text-{TRIP}-abc"
    queue.jobs[mine] = JobState(
        workflow_id=mine,
        workflow_name="parse_expense_text",
        status="SUCCESS",
        owner="auth0|someone-else",
    )
    assert (
        client.get(
            path("get_expense_draft", trip_id=TRIP, workflow_id=mine)
        ).status_code
        == 404
    )


def test_two_members_typing_the_same_sentence_get_their_own_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = FakeJobQueue()
    client = _client(monkeypatch, queue)
    first = client.post(
        path("start_expense_draft", trip_id=TRIP), json=SENTENCE
    ).json()["workflow_id"]
    app = client.app
    assert isinstance(app, FastAPI)
    authorize(app, AuthenticatedUser(sub="auth0|ania"))
    second = client.post(
        path("start_expense_draft", trip_id=TRIP), json=SENTENCE
    ).json()["workflow_id"]
    assert first != second
    # The second member polls their own job, not the first member's.
    _finish(queue, second, {"amount_minor": 14200})
    queue.jobs[second] = queue.jobs[second].model_copy(update={"owner": "auth0|ania"})
    ok = client.get(path("get_expense_draft", trip_id=TRIP, workflow_id=second))
    assert ok.status_code == 200
    assert ok.json()["status"] == "ready"
    other = client.get(path("get_expense_draft", trip_id=TRIP, workflow_id=first))
    assert other.status_code == 404
