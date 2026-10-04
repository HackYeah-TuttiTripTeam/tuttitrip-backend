"""Receipt upload: limits, enqueueing, members-only image, the draft and confirm."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, override
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.shared.fakes import FakeJobQueue, authorize
from tests.shared.paths import path
from tuttitrip.expenses import db
from tuttitrip.expenses.logic.receipts import MAX_BYTES, detect_media_type
from tuttitrip.expenses.models import Expense, ExpenseEvidence, ExpenseShare
from tuttitrip.expenses.schemas import ExpenseStatus, SplitMethod
from tuttitrip.expenses.services import receipt_service
from tuttitrip.expenses.settlement import db as settlement_db
from tuttitrip.main import create_app
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.jobs.api import get_job_queue
from tuttitrip.shared.jobs.contracts import (
    ContractPayload,
    Queue,
    ReadReceiptInput,
    Workflow,
)
from tuttitrip.shared.jobs.schemas import JobState
from tuttitrip.shared.jobs.services.job_queue import (
    JobQueueUnavailableError,
    workflow_id_for,
)
from tuttitrip.shared.jobs.services.worker_liveness import WorkerUnavailableError
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import TripMembership, TripRead, TripRole
from tuttitrip.trips.services import trip_service

TRIP = uuid.uuid4()
KASIA, TOMEK = uuid.UUID(int=1), uuid.UUID(int=2)
ME = AuthenticatedUser(sub="auth0|tomek")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32


def test_signature_decides_the_type() -> None:
    assert detect_media_type(JPEG[:16]) == "image/jpeg"
    assert detect_media_type(PNG[:16]) == "image/png"
    assert detect_media_type(WEBP[:16]) == "image/webp"
    assert detect_media_type(b"%PDF-1.7\n") is None
    assert detect_media_type(b"") is None


@pytest.mark.parametrize(
    ("content", "claimed", "code"),
    [
        (b"", "image/jpeg", "receipt.empty"),
        (b"%PDF-1.7" + b"\x00" * 40, "application/pdf", "receipt.type_not_allowed"),
        (PNG, "image/jpeg", "receipt.type_not_allowed"),  # the claim lies
        (JPEG + b"\x00" * MAX_BYTES, "image/jpeg", "receipt.too_large"),
    ],
)
def test_refused_uploads(content: bytes, claimed: str, code: str) -> None:
    with pytest.raises(receipt_service.ReceiptRejectedError) as caught:
        receipt_service.check_upload(content, claimed)
    assert caught.value.code.value == code


def test_allowed_uploads() -> None:
    assert receipt_service.check_upload(JPEG, "image/jpeg") == "image/jpeg"
    assert receipt_service.check_upload(WEBP, None) == "image/webp"
    big = JPEG + b"\x00" * (3 * 1024 * 1024)
    assert receipt_service.check_upload(big, "image/jpeg") == "image/jpeg"


def _session() -> AsyncMock:
    return AsyncMock()


def _client(
    monkeypatch: pytest.MonkeyPatch,
    queue: FakeJobQueue,
    *,
    member: bool = True,
    worker_up: bool = True,
) -> TestClient:
    def membership(_s: object, _t: uuid.UUID, sub: str, _r: TripRole) -> TripMembership:
        if not member:
            raise trip_service.TripNotFoundError(str(TRIP))
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
        ProfileRead.model_construct(id=i, display_name=n, user_sub=s)
        for i, n, s in ((KASIA, "Kasia", None), (TOMEK, "Tomek", ME.sub))
    ]
    monkeypatch.setattr(
        profile_service, "list_profiles", AsyncMock(return_value=profiles)
    )
    monkeypatch.setattr(
        receipt_service,
        "ensure_worker_available",
        AsyncMock(side_effect=None if worker_up else WorkerUnavailableError("down")),
    )
    monkeypatch.setattr(settlement_db, "select_closure", AsyncMock(return_value=None))

    def stored(_session: object, evidence: ExpenseEvidence) -> None:
        evidence.id = uuid.uuid4()

    monkeypatch.setattr(db, "insert_evidence", AsyncMock(side_effect=stored))
    monkeypatch.setattr(db, "count_evidence", AsyncMock(return_value=0))
    monkeypatch.setattr(db, "delete_expired_evidence", AsyncMock())
    monkeypatch.setattr(db, "delete_evidence", AsyncMock())
    app = create_app()
    authorize(app, ME)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_job_queue] = lambda: queue
    return TestClient(app)


def _upload(
    client: TestClient, content: bytes, mime: str
) -> tuple[int, dict[str, Any]]:
    response = client.post(
        path("upload_receipt", trip_id=TRIP), files={"file": ("r.jpg", content, mime)}
    )
    return response.status_code, response.json()


def test_upload_answers_202_and_enqueues_without_the_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = FakeJobQueue()
    code, body = _upload(_client(monkeypatch, queue), JPEG, "image/jpeg")
    assert code == 202, body
    payload = queue.payloads[body["workflow_id"]]
    assert isinstance(payload, ReadReceiptInput)
    assert str(payload.evidence_id) == body["evidence_id"]
    assert JPEG.hex() not in payload.model_dump_json()
    assert queue.queues[body["workflow_id"]] is Queue.LOCAL_LLM


@pytest.mark.parametrize(
    ("content", "mime"),
    [
        (b"%PDF-1.7" + b"\x00" * 40, "application/pdf"),
        (JPEG + b"\x00" * MAX_BYTES, "image/jpeg"),
    ],
)
def test_pdf_and_oversized_files_answer_422(
    monkeypatch: pytest.MonkeyPatch, content: bytes, mime: str
) -> None:
    queue = FakeJobQueue()
    code, body = _upload(_client(monkeypatch, queue), content, mime)
    assert code == 422
    assert body["detail"][0]["type"].startswith("receipt.")
    assert not queue.jobs


def test_no_worker_answers_503_and_outsiders_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    down = _client(monkeypatch, FakeJobQueue(), worker_up=False)
    assert _upload(down, JPEG, "image/jpeg")[0] == 503
    outsider = _client(monkeypatch, FakeJobQueue(), member=False)
    assert _upload(outsider, JPEG, "image/jpeg")[0] == 404


def _evidence() -> ExpenseEvidence:
    return ExpenseEvidence(
        id=uuid.uuid4(),
        trip_id=TRIP,
        data=JPEG,
        media_type="image/jpeg",
        size=len(JPEG),
        created_by_sub=ME.sub,
        created_at=datetime(2026, 11, 7, tzinfo=UTC),
        delete_after=datetime(2026, 11, 14, tzinfo=UTC),
    )


def test_image_is_served_to_members_only_and_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _evidence()
    monkeypatch.setattr(db, "select_evidence", AsyncMock(return_value=evidence))
    url = path("get_receipt_image", trip_id=TRIP, evidence_id=evidence.id)
    ok = _client(monkeypatch, FakeJobQueue()).get(url)
    assert ok.status_code == 200
    assert ok.content == JPEG
    assert ok.headers["content-type"] == "image/jpeg"
    assert "no-store" in ok.headers["cache-control"]
    assert ok.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert ok.headers["x-content-type-options"] == "nosniff"
    assert (
        _client(monkeypatch, FakeJobQueue(), member=False).get(url).status_code == 404
    )
    monkeypatch.setattr(db, "select_evidence", AsyncMock(return_value=None))
    assert _client(monkeypatch, FakeJobQueue()).get(url).status_code == 404


def test_finished_read_creates_one_draft_that_is_not_settled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _evidence()
    queue = FakeJobQueue()
    client = _client(monkeypatch, queue)
    monkeypatch.setattr(db, "select_evidence", AsyncMock(return_value=evidence))
    monkeypatch.setattr(db, "select_by_evidence", AsyncMock(return_value=None))
    workflow_id = workflow_id_for(
        Workflow.READ_RECEIPT,
        str(evidence.id),
        ReadReceiptInput(trip_id=TRIP, evidence_id=evidence.id),
    )
    url = path("get_receipt", trip_id=TRIP, evidence_id=evidence.id)
    assert client.get(url).json()["status"] == "pending"  # no job yet
    queue.jobs[workflow_id] = JobState(
        workflow_id=workflow_id,
        workflow_name="read_receipt",
        status="SUCCESS",
        owner=ME.sub,
        output={
            "contract_version": 1,
            "amount_minor": 14200,
            "currency": "PLN",
            "spent_on": "2026-11-07",
            "merchant": "Restauracja",
            "category": "food",
            "needs_confirmation": True,
            "reasons": ["sum_mismatch"],
        },
    )

    def inserted(_session: object, expense: Expense) -> None:
        expense.id = uuid.uuid4()
        expense.created_at = datetime(2026, 11, 7, tzinfo=UTC)

    insert = AsyncMock(side_effect=inserted)
    monkeypatch.setattr(db, "insert_expense", insert)
    body = client.get(url).json()
    assert body["status"] == "ready"
    assert (body["needs_confirmation"], body["reasons"]) == (True, ["sum_mismatch"])
    draft = body["expense"]
    assert (draft["status"], draft["has_evidence"]) == ("draft", True)
    assert Decimal(draft["amount"]) == 142
    assert draft["payer_profile_id"] == str(TOMEK)
    saved = insert.call_args.args[1]
    assert isinstance(saved, Expense)
    assert saved.status is ExpenseStatus.DRAFT
    assert saved.evidence_id == evidence.id


def _draft() -> Expense:
    return Expense(
        id=uuid.uuid4(),
        trip_id=TRIP,
        payer_profile_id=TOMEK,
        amount=Decimal("142.00"),
        currency="PLN",
        trip_amount=Decimal("142.00"),
        description="Restauracja",
        spent_on=date(2026, 11, 7),
        category=None,
        split_method=SplitMethod.EQUAL,
        created_by_sub=ME.sub,
        created_at=datetime(2026, 11, 7, tzinfo=UTC),
        status=ExpenseStatus.DRAFT,
        evidence_id=uuid.uuid4(),
        shares=[ExpenseShare(profile_id=KASIA), ExpenseShare(profile_id=TOMEK)],
    )


def test_confirm_makes_it_count_and_deletes_the_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = _draft()
    evidence_id = draft.evidence_id
    client = _client(monkeypatch, FakeJobQueue())
    monkeypatch.setattr(db, "select_expense", AsyncMock(return_value=draft))
    deleted = AsyncMock()
    monkeypatch.setattr(db, "delete_evidence", deleted)
    url = path("confirm_expense", trip_id=TRIP, expense_id=draft.id)
    response = client.post(url, json={"description": "Obiad"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["has_evidence"], body["description"]) == (
        "confirmed",
        False,
        "Obiad",
    )
    assert deleted.await_args is not None
    assert deleted.await_args.args[1] == evidence_id
    # Confirming twice is a conflict.
    assert client.post(url, json={}).status_code == 409


def test_failed_enqueue_deletes_the_image(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(FakeJobQueue):
        @override
        async def enqueue(
            self,
            workflow: Workflow,
            payload: ContractPayload,
            *,
            user: str,
            key: str,
            queue: Queue | None = None,
        ) -> str:
            raise JobQueueUnavailableError

    deleted = AsyncMock()
    client = _client(monkeypatch, Broken())
    monkeypatch.setattr(db, "delete_evidence", deleted)
    assert _upload(client, JPEG, "image/jpeg")[0] == 503
    deleted.assert_awaited_once()


def test_a_trip_cannot_hold_more_unconfirmed_images_than_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = FakeJobQueue()
    client = _client(monkeypatch, queue)
    monkeypatch.setattr(db, "count_evidence", AsyncMock(return_value=20))
    assert _upload(client, JPEG, "image/jpeg")[0] == 429
    assert not queue.jobs
    monkeypatch.setattr(db, "count_evidence", AsyncMock(return_value=19))
    assert _upload(client, JPEG, "image/jpeg")[0] == 202


def test_the_receipt_poll_writes_so_it_needs_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, FakeJobQueue())
    app = client.app
    assert isinstance(app, FastAPI)
    authorize(app, ME, (Grant(Feature.EXPENSES_CORE, Access.READ),))
    url = path("get_receipt", trip_id=TRIP, evidence_id=uuid.uuid4())
    assert client.get(url).status_code == 403
