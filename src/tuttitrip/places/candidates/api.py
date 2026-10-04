"""Candidate fetch endpoints (nested under a trip)."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status

from tuttitrip.places.candidates.schemas import CandidatesRequest, CandidatesStatus
from tuttitrip.places.candidates.services import candidate_service
from tuttitrip.places.candidates.services.candidate_service import (
    CandidatesInputError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.jobs.api import JobQueueDep
from tuttitrip.shared.jobs.schemas import JobAccepted
from tuttitrip.shared.jobs.services.job_queue import JobQueueUnavailableError
from tuttitrip.shared.jobs.services.worker_liveness import WorkerUnavailableError
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripCoHost, TripMember

router = APIRouter(prefix="/trips/{trip_id}/places/candidates", tags=["places"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "Trip not found, or the caller is not on it."}
}
UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    503: {"description": "The worker or the job queue is unavailable."}
}


@router.post(
    "",
    summary="Fetch candidate places for the trip's city",
    description=(
        "Asks the worker to fetch places of the trip's city from OpenStreetMap "
        "into the catalog. 202 with the job to poll; asking again for the same "
        "city returns the same job. A city that already has places (the demo "
        "cities) answers 200 with `state: ready` and starts nothing. 422 when "
        "the trip has no city; 503 while the worker is missing. Candidates have "
        "few hours and prices, so their plan items stay unverified."
    ),
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **NOT_FOUND,
        **UNAVAILABLE,
        200: {"model": CandidatesStatus, "description": "The city has places."},
        422: {"description": "The trip has no city, or `city_query` is for another."},
    },
    dependencies=[requires(Feature.PLACES_CANDIDATES, Access.WRITE)],
)
async def request_candidates(
    membership: TripCoHost,
    session: SessionDep,
    queue: JobQueueDep,
    response: Response,
    data: CandidatesRequest | None = None,
) -> JobAccepted | CandidatesStatus:
    """Start fetching candidates.

    Args:
        membership: The caller's membership of ``{trip_id}`` (co-host or host).
        session: Database session.
        queue: Job queue.
        response: Used to answer 200 for a city that needs no job.
        data: Optional city name for a never-fetched city.

    Returns:
        The job, or the status of a city that already has places.

    Raises:
        HTTPException: 422 for an input problem, 503 without a worker.
    """
    try:
        result = await candidate_service.request_candidates(
            session, queue, membership, data or CandidatesRequest()
        )
    except CandidatesInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except WorkerUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except JobQueueUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue unavailable"
        ) from exc
    if isinstance(result, CandidatesStatus):
        response.status_code = status.HTTP_200_OK
    return result


@router.get(
    "/status",
    summary="State of the candidate fetch and size of the city's catalog",
    responses={
        **NOT_FOUND,
        **UNAVAILABLE,
        422: {"description": "No city, or a foreign job."},
    },
    dependencies=[requires(Feature.PLACES_CATALOG, Access.READ)],
)
async def candidates_status(
    membership: TripMember,
    session: SessionDep,
    queue: JobQueueDep,
    job_id: Annotated[
        str | None, Query(description="The job id from the 202 (or the 409).")
    ] = None,
) -> CandidatesStatus:
    """Catalog size and, with `job_id`, the state of the job.

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.
        queue: Job queue.
        job_id: The job to report on.

    Returns:
        The status.

    Raises:
        HTTPException: 422 for a job of another city, 503 when the queue is down.
    """
    try:
        return await candidate_service.candidates_status(
            session, queue, membership, job_id
        )
    except CandidatesInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except JobQueueUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue unavailable"
        ) from exc
