"""Lint our plan version, or a plan pasted from another tool.

A pasted plan goes through the worker (``parse_pasted_plan``: the quotes, the
times, the catalog match), then pure code builds the lint plan and the rules
decide. The parse is kept when the job succeeds and the report is stored once;
a host's pick of a candidate recomputes the report from the stored parse, with
no new job.
"""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.linter import db as documents_db
from tuttitrip.planning.linter.logic.rules import lint
from tuttitrip.planning.linter.schemas import DocumentKind, LintContext, LintReport
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.schemas import PlanningInput
from tuttitrip.planning.trip_linter import db
from tuttitrip.planning.trip_linter.logic.context import lint_context
from tuttitrip.planning.trip_linter.logic.paste_lint import paste_to_lint
from tuttitrip.planning.trip_linter.logic.plan_lint import plan_to_lint
from tuttitrip.planning.trip_linter.models import PasteCheck
from tuttitrip.planning.trip_linter.schemas import (
    PasteAccepted,
    PasteCheckRead,
    PasteCreate,
    PasteState,
    StoredReport,
)
from tuttitrip.profiles.preferences.services import preference_service
from tuttitrip.shared.jobs.contracts import (
    ParsePastedPlanInput,
    ParsePastedPlanOutput,
    Workflow,
    queue_for,
)
from tuttitrip.shared.jobs.services.job_queue import JobNotFoundError, JobQueue
from tuttitrip.shared.jobs.services.worker_liveness import ensure_worker_available
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

_RUNNING = frozenset({"ENQUEUED", "DELAYED", "PENDING"})


class PasteNotFoundError(Exception):
    """The trip has no such pasted plan check."""


class PasteInputError(Exception):
    """The trip cannot take a pasted plan, or the pick is not a candidate."""


class PasteNotReadyError(Exception):
    """The worker has not parsed the text (yet, or it failed)."""


async def _context(
    session: AsyncSession, membership: TripMembership
) -> tuple[PlanningInput, LintContext]:
    # Host-level view: the rules read everybody's limits, like the plan does.
    host = membership.model_copy(update={"role": TripRole.HOST})
    data, names, _ = await plan_service.gather_input(session, membership)
    preferences = await preference_service.list_preferences(session, host)
    wheelchair = [
        p.profile_id
        for p in preferences
        if p.constraints is not None and p.constraints.wheelchair
    ]
    return data, lint_context(data, names, wheelchair)


async def check_plan(
    session: AsyncSession,
    queue: JobQueue,
    membership: TripMembership,
    plan_id: UUID,
) -> LintReport:
    """Lint a stored plan version of the trip (no worker involved).

    Args:
        session: Open session.
        queue: Job queue (the plan read may ask for justifications).
        membership: Proof from ``TripAccess``.
        plan_id: The version to check.

    Returns:
        The report with every rule.

    Raises:
        PlanNotFoundError: The trip has no such version (from the plan service).
        PlanInputError: The trip lacks dates, a city or people.
        CatalogMissingError: The city has no places.
    """
    plan = await plan_service.get_plan(session, membership, plan_id, queue=queue)
    data, context = await _context(session, membership)
    return lint(plan_to_lint(plan, data.trip.days[0]), context)


async def create_paste(
    session: AsyncSession,
    queue: JobQueue,
    membership: TripMembership,
    data: PasteCreate,
) -> PasteAccepted:
    """Store the pasted text and queue the parse.

    The text is saved before the worker is asked, so a missing worker (503)
    does not lose it.

    Args:
        session: Open session.
        queue: Job queue.
        membership: Proof from ``TripAccess`` (co-host or host).
        data: The text.

    Returns:
        The id to poll and the job.

    Raises:
        PasteInputError: The trip has no city.
        WorkerUnavailableError: No worker is running (from the guard).
        JobQueueUnavailableError: The job queue is down.
    """
    trip = await trip_service.get_trip(session, membership)
    if trip.city_slug is None or trip.start_date is None:
        msg = "The trip needs a city and dates to check a plan against"
        raise PasteInputError(msg)
    document = await documents_db.insert_document(
        session,
        trip_id=membership.trip_id,
        kind=DocumentKind.PLAN.value,
        text=data.text,
        created_by=membership.sub,
    )
    await session.commit()
    await ensure_worker_available(session)
    payload = ParsePastedPlanInput(
        trip_id=membership.trip_id,
        document_id=document.id,
        city_slug=trip.city_slug,
        provider=data.provider,
    )
    job_id = await queue.enqueue(
        Workflow.PARSE_PASTED_PLAN,
        payload,
        user=membership.sub,
        key=str(document.id),
        queue=queue_for(data.provider),
    )
    await db.insert_check(
        session,
        PasteCheck(
            id=document.id,
            trip_id=membership.trip_id,
            job_id=job_id,
            created_by=membership.sub,
        ),
    )
    await session.commit()
    return PasteAccepted(workflow_id=job_id, paste_id=document.id)


async def _build(
    session: AsyncSession, membership: TripMembership, row: PasteCheck
) -> PasteCheckRead:
    """Lint the stored parse with the host's picks; keep the report.

    Returns:
        The check with the new report.
    """
    parsed = ParsePastedPlanOutput.model_validate(row.parsed)
    data, context = await _context(session, membership)
    picks = {int(index): UUID(place) for index, place in row.picks.items()}
    plan, items = paste_to_lint(
        parsed,
        picks,
        first_day=data.trip.days[0],
        day_start=data.trip.day_start,
        group_size=len(data.people),
        known_places={p.id for p in context.places},
    )
    stored = StoredReport(lint=lint(plan, context), items=items)
    row.report = stored.model_dump(mode="json")
    await session.commit()
    return _read(row, stored, parsed)


def _read(
    row: PasteCheck, stored: StoredReport | None, parsed: ParsePastedPlanOutput | None
) -> PasteCheckRead:
    state = PasteState.DONE if stored else PasteState.PENDING
    if row.job_failed:
        state = PasteState.FAILED
    return PasteCheckRead(
        paste_id=row.id,
        trip_id=row.trip_id,
        state=state,
        job_id=row.job_id,
        error_code=row.error_code,
        violations=None if stored is None else stored.lint.count,
        report=None if stored is None else stored.lint,
        items=[] if stored is None else stored.items,
        unread=[] if parsed is None else parsed.unread,
    )


async def _settle_job(queue: JobQueue, session: AsyncSession, row: PasteCheck) -> None:
    """Take the worker's parse (once) or note that the job failed."""
    try:
        job = await queue.get(row.job_id)
    except JobNotFoundError:
        row.job_failed = True
        await session.commit()
        return
    if job.status in _RUNNING:
        return
    try:
        if job.status != "SUCCESS":
            raise ValueError(job.status)  # ruff: ignore[raise-within-try]
        row.parsed = ParsePastedPlanOutput.model_validate(job.output or {}).model_dump(
            mode="json"
        )
    except ValidationError, ValueError:
        row.job_failed, row.error_code = True, job.error_code
    await session.commit()


async def get_paste(
    session: AsyncSession,
    queue: JobQueue,
    membership: TripMembership,
    paste_id: UUID,
) -> PasteCheckRead:
    """The check of a pasted plan; the first read after the job builds the report.

    The report is stored then and returned unchanged afterwards, because the
    catalog may change; a pick (``pick_item``) recomputes it.

    Args:
        session: Open session.
        queue: Job queue.
        membership: Proof from ``TripAccess`` (any member).
        paste_id: The id from the 202.

    Returns:
        The state and, once the worker is done, the report.

    Raises:
        PasteNotFoundError: The trip has no such check.
        PlanInputError: The trip lacks dates, a city or people.
        CatalogMissingError: The city has no places.
        JobQueueUnavailableError: The job queue is down.
    """
    row = await db.select_check(session, membership.trip_id, paste_id)
    if row is None:
        raise PasteNotFoundError(str(paste_id))
    if row.parsed is None and not row.job_failed:
        await _settle_job(queue, session, row)
    if row.parsed is None:
        return _read(row, None, None)
    if row.report is not None:  # stored once: a later catalog change does not move it
        return _read(
            row,
            StoredReport.model_validate(row.report),
            ParsePastedPlanOutput.model_validate(row.parsed),
        )
    return await _build(session, membership, row)


async def pick_item(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] the item of a check
    session: AsyncSession,
    queue: JobQueue,
    membership: TripMembership,
    paste_id: UUID,
    index: int,
    place_id: UUID,
) -> PasteCheckRead:
    """Record the host's choice for an unsure item and recompute the report.

    Args:
        session: Open session.
        queue: Job queue.
        membership: Proof from ``TripAccess`` (co-host or host).
        paste_id: The check.
        index: Item index in the text.
        place_id: A candidate of the item.

    Returns:
        The check with the new report.

    Raises:
        PasteNotFoundError: No such check.
        PasteNotReadyError: The text is not parsed.
        PasteInputError: No such item, or the place is not one of its candidates.
        PlanInputError: The trip lacks dates, a city or people.
    """
    row = await db.select_check(session, membership.trip_id, paste_id)
    if row is None:
        raise PasteNotFoundError(str(paste_id))
    if row.parsed is None and not row.job_failed:
        await _settle_job(queue, session, row)
    if row.parsed is None:
        msg = "The pasted plan is not parsed"
        raise PasteNotReadyError(msg)
    parsed = ParsePastedPlanOutput.model_validate(row.parsed)
    match = next((m for m in parsed.matches if m.item_index == index), None)
    allowed = set() if match is None else {c.place_id for c in match.candidates}
    if match is not None and match.place_id:
        allowed.add(match.place_id)
    if str(place_id) not in allowed:
        msg = "The place is not a candidate of this item"
        raise PasteInputError(msg)
    row.picks = {**row.picks, str(index): str(place_id)}
    await session.commit()
    return await _build(session, membership, row)
