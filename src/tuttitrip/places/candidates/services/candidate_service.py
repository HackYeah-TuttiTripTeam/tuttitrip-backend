"""Ask the worker for the candidate places of a city outside the catalog."""

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.places import db
from tuttitrip.places.candidates.logic.slug import slugify
from tuttitrip.places.candidates.schemas import (
    CandidatesRequest,
    CandidatesState,
    CandidatesStatus,
)
from tuttitrip.shared.jobs.contracts import FetchPlaceCandidatesInput, Workflow
from tuttitrip.shared.jobs.schemas import JobAccepted
from tuttitrip.shared.jobs.services.job_queue import (
    JobNotFoundError,
    JobQueue,
    JobQueueUnavailableError,
)
from tuttitrip.shared.jobs.services.worker_liveness import (
    WorkerUnavailableError,
    ensure_worker_available,
)
from tuttitrip.trips.schemas import TripMembership
from tuttitrip.trips.services import trip_service

_RUNNING = frozenset({"ENQUEUED", "DELAYED", "PENDING"})


class CandidatesInputError(Exception):
    """The request cannot be served: no city, a name for another city, a foreign job."""


async def _slug_of(session: AsyncSession, membership: TripMembership) -> str:
    trip = await trip_service.get_trip(session, membership)
    if trip.city_slug is None:
        msg = "The trip needs a city"
        raise CandidatesInputError(msg)
    return trip.city_slug


async def _enqueue(
    session: AsyncSession,
    queue: JobQueue,
    sub: str,
    slug: str,
    city_query: str | None,
) -> str:
    """Enqueue the fetch of one city; the workflow id repeats for the same request.

    A city the catalog already lists is fetched by slug; a new one by name
    (the worker geocodes it), defaulting to the slug's words.

    Returns:
        The workflow id.
    """
    if await db.city_exists(session, slug):
        payload = FetchPlaceCandidatesInput(city_slug=slug)
    else:
        query = city_query or slug.replace("-", " ")
        if slugify(query) != slug:
            msg = f"city_query must give the slug '{slug}'"
            raise CandidatesInputError(msg)
        payload = FetchPlaceCandidatesInput(city_query=query)
    await ensure_worker_available(session)
    return await queue.enqueue(
        Workflow.FETCH_PLACE_CANDIDATES, payload, user=sub, key=slug
    )


async def request_candidates(
    session: AsyncSession,
    queue: JobQueue,
    membership: TripMembership,
    data: CandidatesRequest,
) -> JobAccepted | CandidatesStatus:
    """Start the fetch for the trip's city, unless its catalog already has places.

    The demo cities (from the sheet) have places, so they answer ``ready`` and
    start nothing. Asking again for the same city returns the same job.

    Args:
        session: Open session.
        queue: Job queue.
        membership: Proof from ``TripAccess`` (co-host or host).
        data: Optional city name for a city never fetched.

    Returns:
        The job to poll, or the ``ready`` status of a city with places.

    Raises:
        CandidatesInputError: No city on the trip, or a name for another city.
        WorkerUnavailableError: No worker is running (from the guard).
        JobQueueUnavailableError: The job queue is down.
    """
    slug = await _slug_of(session, membership)
    count = await db.count_places(session, slug)
    if count:
        return CandidatesStatus(
            city_slug=slug, place_count=count, state=CandidatesState.READY
        )
    job_id = await _enqueue(session, queue, membership.sub, slug, data.city_query)
    return JobAccepted(workflow_id=job_id)


async def job_for_missing_catalog(
    session: AsyncSession, queue: JobQueue, sub: str, slug: str
) -> str | None:
    """Start (or find) the fetch for a city whose catalog is empty; never fails.

    Used when a plan is asked for a city without places: the answer should name
    the job, but a missing worker must not hide the real reason.

    Args:
        session: Open session.
        queue: Job queue.
        sub: Auth0 subject of the caller.
        slug: The trip's city.

    Returns:
        The workflow id, or None when no job could be started.
    """
    try:
        return await _enqueue(session, queue, sub, slug, None)
    except CandidatesInputError, WorkerUnavailableError, JobQueueUnavailableError:
        return None


async def candidates_status(
    session: AsyncSession,
    queue: JobQueue,
    membership: TripMembership,
    job_id: str | None,
) -> CandidatesStatus:
    """The catalog size of the trip's city and, with a job id, the job's state.

    Args:
        session: Open session.
        queue: Job queue.
        membership: Proof from ``TripAccess`` (any member).
        job_id: The id from the 202 (or the 409); must be a fetch of this city.

    Returns:
        The status.

    Raises:
        CandidatesInputError: No city on the trip, or a job of another city.
        JobQueueUnavailableError: The job queue is down.
    """
    slug = await _slug_of(session, membership)
    count = await db.count_places(session, slug)
    state = CandidatesState.READY if count else CandidatesState.EMPTY
    error_code = error = None
    if job_id is not None:
        if not job_id.startswith(f"{Workflow.FETCH_PLACE_CANDIDATES.value}-{slug}-"):
            msg = "The job is not a fetch of this trip's city"
            raise CandidatesInputError(msg)
        try:
            job = await queue.get(job_id)
        except JobNotFoundError:
            job = None
        if job is not None and job.status in _RUNNING and not count:
            state = CandidatesState.RUNNING
        elif job is not None and job.status not in {*_RUNNING, "SUCCESS"} and not count:
            state = CandidatesState.FAILED
            error_code, error = job.error_code, job.error
    return CandidatesStatus(
        city_slug=slug,
        place_count=count,
        state=state,
        job_id=job_id,
        error_code=error_code,
        error=error,
    )
