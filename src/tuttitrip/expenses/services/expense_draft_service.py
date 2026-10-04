"""Draft an expense from one typed sentence (the model runs in the worker)."""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.expenses.logic.names import resolve_people
from tuttitrip.expenses.logic.split import CENTS
from tuttitrip.expenses.schemas import (
    DraftIssueRead,
    ExpenseDraft,
    ExpenseDraftState,
    ExpenseTextRequest,
)
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.jobs.contracts import (
    ParseExpenseTextInput,
    ParseExpenseTextOutput,
    Workflow,
)
from tuttitrip.shared.jobs.services.job_queue import JobNotFoundError, JobQueue
from tuttitrip.shared.jobs.services.worker_liveness import ensure_worker_available
from tuttitrip.trips.schemas import TripMembership
from tuttitrip.trips.services import trip_service

FAILED = {"ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"}


class DraftNotFoundError(Exception):
    """No such draft job for this trip and caller."""


async def start_draft(
    session: AsyncSession,
    queue: JobQueue,
    membership: TripMembership,
    data: ExpenseTextRequest,
) -> str:
    """Check the worker and enqueue ``parse_expense_text``.

    Args:
        session: Open session.
        queue: Job queue.
        membership: The caller's checked membership of the trip.
        data: The sentence.

    Returns:
        The workflow id (the same one for the same sentence).
    """
    await ensure_worker_available(session)
    payload = ParseExpenseTextInput(trip_id=membership.trip_id, text=data.text)
    return await queue.enqueue(
        Workflow.PARSE_EXPENSE_TEXT,
        payload,
        user=membership.sub,
        key=str(membership.trip_id),
    )


async def get_draft(
    session: AsyncSession,
    queue: JobQueue,
    membership: TripMembership,
    workflow_id: str,
) -> ExpenseDraftState:
    """Turn the finished job into a draft: names matched to the trip's people.

    Args:
        session: Open session.
        queue: Job queue.
        membership: The caller's checked membership of the trip.
        workflow_id: Id returned by ``start_draft``.

    Returns:
        ``pending`` while it runs, ``failed`` if it did not read, else the draft.

    Raises:
        DraftNotFoundError: Not a draft job of this trip and caller.
    """
    prefix = f"{Workflow.PARSE_EXPENSE_TEXT.value}-{membership.trip_id}-"
    if not workflow_id.startswith(prefix):
        raise DraftNotFoundError(workflow_id)
    try:
        job = await queue.get(workflow_id)
    except JobNotFoundError as exc:
        raise DraftNotFoundError(workflow_id) from exc
    if job.owner != membership.sub:
        raise DraftNotFoundError(workflow_id)
    if job.status in FAILED:
        return ExpenseDraftState(status="failed", draft=None)
    if job.status != "SUCCESS":
        return ExpenseDraftState(status="pending", draft=None)
    try:
        parsed = ParseExpenseTextOutput.model_validate(job.output)
    except ValidationError:
        return ExpenseDraftState(status="failed", draft=None)
    return ExpenseDraftState(
        status="ready", draft=await _draft(session, membership, parsed)
    )


async def _draft(
    session: AsyncSession, membership: TripMembership, parsed: ParseExpenseTextOutput
) -> ExpenseDraft:
    trip = await trip_service.get_trip(session, membership)
    profiles = await profile_service.list_profiles(session, membership)
    caller = next((p.id for p in profiles if p.user_sub == membership.sub), None)
    people = resolve_people(
        payer_name=parsed.payer_name,
        included=parsed.included_names,
        excluded=parsed.excluded_names,
        people={p.id: p.display_name for p in profiles},
        caller=caller,
        confidence=parsed.confidence,
    )
    issues = [
        DraftIssueRead(code=i.code, name=i.name, candidates=list(i.candidates))  # ty: ignore[invalid-argument-type] codes are the Literal
        for i in people.issues
    ]
    return ExpenseDraft(
        amount=Decimal(parsed.amount_minor) / CENTS,
        currency=parsed.currency or str(trip.currency or ""),
        description=parsed.description,
        spent_on=datetime.now(UTC).date(),
        payer_profile_id=people.payer,
        participants=people.participants,
        needs_confirmation=bool(issues),
        issues=issues,
    )
