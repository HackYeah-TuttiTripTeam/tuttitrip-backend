"""Receipt photos and bank screenshots: store, enqueue the read, make the draft."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.expenses import db
from tuttitrip.expenses.logic.receipts import ALLOWED, MAX_BYTES, detect_media_type
from tuttitrip.expenses.logic.split import CENTS
from tuttitrip.expenses.models import ExpenseEvidence
from tuttitrip.expenses.schemas import (
    ExpenseCategory,
    ExpenseCreate,
    ExpenseRead,
    ReceiptAccepted,
    ReceiptErrorCode,
    ReceiptState,
    ShareInput,
)
from tuttitrip.expenses.services import expense_service
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.jobs.contracts import (
    ReadReceiptInput,
    ReadReceiptOutput,
    Workflow,
)
from tuttitrip.shared.jobs.services.job_queue import (
    JobNotFoundError,
    JobQueue,
    workflow_id_for,
)
from tuttitrip.shared.jobs.services.worker_liveness import ensure_worker_available
from tuttitrip.trips.schemas import TripMembership
from tuttitrip.trips.services import trip_service

KEEP_UNCONFIRMED = timedelta(days=7)
FAILED = {"ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"}
HEAD = 16


class ReceiptRejectedError(Exception):
    """The upload is empty, too big or not a JPEG, PNG or WebP image."""

    def __init__(self, code: ReceiptErrorCode, message: str) -> None:
        """Keep the code for the API to report.

        Args:
            code: Stable error code.
            message: For people.
        """
        super().__init__(message)
        self.code = code
        self.message = message


class ReceiptNotFoundError(Exception):
    """No such receipt on this trip (or it was already confirmed and deleted)."""


def check_upload(content: bytes, claimed_type: str | None) -> str:
    """Validate size and type of an upload.

    Args:
        content: The bytes read (at most ``MAX_BYTES + 1``).
        claimed_type: The ``Content-Type`` of the part.

    Returns:
        The media type, taken from the file signature.

    Raises:
        ReceiptRejectedError: Empty, over the limit or not an allowed image.
    """
    if not content:
        raise ReceiptRejectedError(ReceiptErrorCode.EMPTY, "The file is empty")
    if len(content) > MAX_BYTES:
        msg = f"The file is larger than {MAX_BYTES // 1024 // 1024} MB"
        raise ReceiptRejectedError(ReceiptErrorCode.TOO_LARGE, msg)
    detected = detect_media_type(content[:HEAD])
    if detected not in ALLOWED or claimed_type not in {None, detected}:
        msg = "Only JPEG, PNG and WebP images are accepted"
        raise ReceiptRejectedError(ReceiptErrorCode.TYPE_NOT_ALLOWED, msg)
    return detected


async def upload(
    session: AsyncSession,
    queue: JobQueue,
    membership: TripMembership,
    content: bytes,
    claimed_type: str | None,
) -> ReceiptAccepted:
    """Store the image and enqueue ``read_receipt`` (the image stays in the database).

    Args:
        session: Open session.
        queue: Job queue.
        membership: The caller's checked membership of the trip.
        content: The uploaded bytes.
        claimed_type: The ``Content-Type`` of the part.

    Returns:
        The workflow id and the evidence id.

    Raises:
        ReceiptRejectedError: The file is refused.
    """
    media_type = check_upload(content, claimed_type)
    await ensure_worker_available(session)
    evidence = ExpenseEvidence(
        trip_id=membership.trip_id,
        data=content,
        media_type=media_type,
        size=len(content),
        created_by_sub=membership.sub,
        delete_after=datetime.now(UTC) + KEEP_UNCONFIRMED,
    )
    await db.insert_evidence(session, evidence)
    await session.commit()
    workflow_id = await queue.enqueue(
        Workflow.READ_RECEIPT,
        _payload(evidence),
        user=membership.sub,
        key=str(evidence.id),
    )
    return ReceiptAccepted(workflow_id=workflow_id, evidence_id=evidence.id)


def _payload(evidence: ExpenseEvidence) -> ReadReceiptInput:
    return ReadReceiptInput(trip_id=evidence.trip_id, evidence_id=evidence.id)


async def get_image(
    session: AsyncSession, membership: TripMembership, evidence_id: UUID
) -> ExpenseEvidence:
    """The stored image, for members of the trip only.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        evidence_id: Evidence id.

    Returns:
        The evidence row.

    Raises:
        ReceiptNotFoundError: Not on this trip, or already deleted.
    """
    evidence = await db.select_evidence(session, membership.trip_id, evidence_id)
    if evidence is None:
        raise ReceiptNotFoundError(str(evidence_id))
    return evidence


async def get_state(
    session: AsyncSession,
    queue: JobQueue,
    membership: TripMembership,
    evidence_id: UUID,
) -> ReceiptState:
    """Progress of the read; on success the draft expense is created once.

    Args:
        session: Open session.
        queue: Job queue.
        membership: The caller's checked membership of the trip.
        evidence_id: Evidence id from the upload.

    Returns:
        ``pending``, ``failed`` or ``ready`` with the draft.

    Raises:
        ReceiptNotFoundError: Not on this trip, or already deleted.
    """
    evidence = await get_image(session, membership, evidence_id)
    existing = await db.select_by_evidence(session, membership.trip_id, evidence_id)
    if existing is not None:
        return _ready(expense_service.read(existing))
    workflow_id = workflow_id_for(
        Workflow.READ_RECEIPT, str(evidence.id), _payload(evidence)
    )
    try:
        job = await queue.get(workflow_id)
    except JobNotFoundError:
        return _waiting("pending")
    if job.status in FAILED:
        return _waiting("failed")
    if job.status != "SUCCESS":
        return _waiting("pending")
    try:
        output = ReadReceiptOutput.model_validate(job.output)
    except ValueError:
        return _waiting("failed")
    return await _draft_from(session, membership, evidence_id, output)


async def _draft_from(
    session: AsyncSession,
    membership: TripMembership,
    evidence_id: UUID,
    output: ReadReceiptOutput,
) -> ReceiptState:
    try:
        data = await _expense_from(session, membership, output)
        draft = await expense_service.create_draft(
            session, membership, data, evidence_id
        )
    except ValueError, expense_service.ExpenseInvalidError:
        return _waiting("failed")
    except IntegrityError:  # a parallel poll created it first
        await session.rollback()
        again = await db.select_by_evidence(session, membership.trip_id, evidence_id)
        if again is None:
            return _waiting("failed")
        return _ready(expense_service.read(again))
    return _ready(draft, needs=output.needs_confirmation, reasons=output.reasons)


def _waiting(status: Literal["pending", "failed"]) -> ReceiptState:
    return ReceiptState(
        status=status, expense=None, needs_confirmation=None, reasons=[]
    )


def _ready(
    expense: ExpenseRead, *, needs: bool | None = None, reasons: list[str] | None = None
) -> ReceiptState:
    return ReceiptState(
        status="ready",
        expense=expense,
        needs_confirmation=needs,
        reasons=reasons or [],
    )


async def _expense_from(
    session: AsyncSession, membership: TripMembership, output: ReadReceiptOutput
) -> ExpenseCreate:
    """The read fields as a new expense: the caller pays, everybody shares.

    Returns:
        The expense to store as a draft.
    """
    trip = await trip_service.get_trip(session, membership)
    profiles = sorted(
        await profile_service.list_profiles(session, membership), key=lambda p: p.id.int
    )
    payer = next((p.id for p in profiles if p.user_sub == membership.sub), None)
    return ExpenseCreate(
        payer_profile_id=payer or profiles[0].id,
        amount=Decimal(output.amount_minor) / CENTS,
        currency=output.currency or trip.currency,
        description=output.merchant or "",
        spent_on=(
            date.fromisoformat(output.spent_on)
            if output.spent_on
            else datetime.now(UTC).date()
        ),
        category=ExpenseCategory(output.category) if output.category else None,
        participants=[ShareInput(profile_id=p.id) for p in profiles],
    )
