"""The "anyway" suggestions of a plan: what the host sees and what he decided.

The suggestions are computed with the plan (``plan_service``) and stored in its
result. Here they are filtered by the host's rejections, marked accepted when
the place became a "must", and get the model's text when the worker has written
it (``write_justifications``, backend#100 and worker#27). Without the model the
template stays, so the plan works without it.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.anyway import db
from tuttitrip.planning.anyway.logic.constants import JOB_USER
from tuttitrip.planning.anyway.schemas import AnywayRead
from tuttitrip.planning.overrides import db as overrides_db
from tuttitrip.planning.plans import db as plans_db
from tuttitrip.planning.plans.schemas import AnywayStatus, AnywaySuggestion
from tuttitrip.shared.jobs.contracts import (
    Workflow,
    WriteJustificationsInput,
    WriteJustificationsOutput,
    queue_for,
)
from tuttitrip.shared.jobs.services.job_queue import (
    JobNotFoundError,
    JobQueue,
    JobQueueUnavailableError,
    workflow_id_for,
)
from tuttitrip.shared.jobs.services.worker_liveness import (
    WorkerUnavailableError,
    ensure_worker_available,
)
from tuttitrip.trips.schemas import TripMembership

SUCCESS = "SUCCESS"


class AnywayNotFoundError(Exception):
    """The plan version has no such suggestion (or no such plan)."""


async def rejected_pairs(
    session: AsyncSession, trip_id: UUID
) -> frozenset[tuple[int, UUID]]:
    """The (day, place) pairs the host rejected, to leave out of a new plan.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The pairs.
    """
    return frozenset(await db.select_rejected(session, trip_id))


async def visible(
    session: AsyncSession,
    trip_id: UUID,
    plan_id: UUID,
    stored: list[AnywaySuggestion],
) -> list[AnywaySuggestion]:
    """The stored suggestions as the host sees them now.

    Args:
        session: Open session.
        trip_id: Trip id.
        plan_id: Plan version id.
        stored: The suggestions in the plan's result.

    Returns:
        Without the rejected ones, with the model's text where it is there and
        ``accepted`` where the place is a "must".
    """
    if not stored:
        return []
    rejected = await db.select_rejected(session, trip_id)
    texts = await db.select_justifications(session, plan_id)
    musts = {
        o.place_id
        for o in await overrides_db.select_active(session, trip_id)
        if o.kind == "must"
    }
    shown: list[AnywaySuggestion] = []
    for item in stored:
        key = (item.day, item.place_id)
        if key in rejected:
            continue
        update: dict[str, object] = {}
        if key in texts:
            update |= {"justification": texts[key], "justification_source": "model"}
        if item.place_id in musts:
            update["status"] = AnywayStatus.ACCEPTED
        shown.append(item.model_copy(update=update))
    return shown


def _stored_suggestions(result: dict[str, Any]) -> list[AnywaySuggestion]:
    raw: list[Any] = result.get("anyway") or []
    return [AnywaySuggestion.model_validate(x) for x in raw]


async def request_justifications(
    session: AsyncSession,
    queue: JobQueue,
    plan_id: UUID,
    suggestions: list[AnywaySuggestion],
    *,
    owner: str = JOB_USER,
) -> bool:
    """Ask the worker to write the justifications of a plan version.

    The job id is deterministic, so asking twice starts one run. A missing worker
    or queue is not an error: the template stays.

    Args:
        session: Open session.
        queue: Job queue.
        plan_id: Plan version id.
        suggestions: The version's suggestions (nothing is asked when empty).
        owner: Auth0 subject recorded as the owner of the job.

    Returns:
        Whether the job was enqueued.
    """
    if not suggestions:
        return False
    payload = WriteJustificationsInput(plan_id=plan_id)
    try:
        await ensure_worker_available(session)
        await queue.enqueue(
            Workflow.WRITE_JUSTIFICATIONS,
            payload,
            user=owner,
            key=str(plan_id),
            queue=queue_for(payload.provider),
        )
    except WorkerUnavailableError, JobQueueUnavailableError:
        return False
    return True


async def _refresh(
    session: AsyncSession,
    queue: JobQueue,
    trip_id: UUID,
    plan_id: UUID,
    suggestions: list[AnywaySuggestion],
) -> None:
    workflow_id = workflow_id_for(
        Workflow.WRITE_JUSTIFICATIONS,
        str(plan_id),
        WriteJustificationsInput(plan_id=plan_id),
    )
    try:
        state = await queue.get(workflow_id)
    except JobNotFoundError, JobQueueUnavailableError:
        return
    if state.status != SUCCESS or state.output is None:
        return
    try:
        output = WriteJustificationsOutput.model_validate(state.output)
    except ValidationError:
        return
    texts = {
        j.place_id: j.text
        for j in output.justifications
        if j.profile_id is None and j.source == "model"
    }
    found = False
    for item in suggestions:
        text = texts.get(str(item.place_id))
        if text is not None:
            found = True
            await db.upsert_state(
                session,
                trip_id=trip_id,
                plan_id=plan_id,
                day=item.day,
                place_id=item.place_id,
                changes={"justification": text},
            )
    if found:
        await session.commit()


async def read(
    session: AsyncSession,
    membership: TripMembership,
    queue: JobQueue,
    plan_id: UUID,
) -> AnywayRead:
    """The suggestions of a plan version; fetches the model's text if it is ready.

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.
        queue: Job queue.
        plan_id: Plan version id.

    Returns:
        The suggestions the host sees.

    Raises:
        AnywayNotFoundError: When the trip has no such version.
    """
    row = await plans_db.select_by_id(session, membership.trip_id, plan_id)
    if row is None:
        raise AnywayNotFoundError(str(plan_id))
    stored = _stored_suggestions(row.result)
    have = await db.select_justifications(session, plan_id)
    missing = [s for s in stored if (s.day, s.place_id) not in have]
    if missing:
        # Idempotent (same job id): covers a worker that was down at generation.
        await request_justifications(
            session, queue, plan_id, missing, owner=membership.sub
        )
        await _refresh(session, queue, membership.trip_id, plan_id, missing)
    shown = await visible(session, membership.trip_id, plan_id, stored)
    return AnywayRead(
        plan_id=plan_id,
        suggestions=shown,
        justification_pending=any(s.justification_source == "template" for s in shown),
    )


async def reject(
    session: AsyncSession,
    membership: TripMembership,
    queue: JobQueue,
    plan_id: UUID,
    place_id: UUID,
) -> AnywayRead:
    """The host does not want this suggestion; it does not return on its day.

    Args:
        session: Open session.
        membership: The host's membership.
        queue: Job queue.
        plan_id: Plan version id.
        place_id: The suggested place.

    Returns:
        The suggestions that remain.

    Raises:
        AnywayNotFoundError: When the version has no such suggestion.
    """
    row = await plans_db.select_by_id(session, membership.trip_id, plan_id)
    if row is None:
        raise AnywayNotFoundError(str(plan_id))
    target = next(
        (s for s in _stored_suggestions(row.result) if s.place_id == place_id), None
    )
    if target is None:
        raise AnywayNotFoundError(str(place_id))
    await db.upsert_state(
        session,
        trip_id=membership.trip_id,
        plan_id=plan_id,
        day=target.day,
        place_id=place_id,
        changes={"rejected_at": datetime.now(UTC), "rejected_by_sub": membership.sub},
    )
    await session.commit()
    return await read(session, membership, queue, plan_id)
