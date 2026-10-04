"""Accommodation endpoints (nested under a trip)."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tuttitrip.accommodation.schemas import (
    OfferCreate,
    OfferRead,
    OpeningQuery,
    RequirementsRead,
    RequirementsWrite,
    SearchLinksRead,
    SearchOpeningRead,
    SearchOpenWrite,
)
from tuttitrip.accommodation.services import (
    offer_service,
    requirements_service,
    search_links_service,
)
from tuttitrip.accommodation.services.offer_service import (
    OfferInvalidError,
    OfferNotFoundError,
)
from tuttitrip.accommodation.services.requirements_service import (
    RequirementsInvalidError,
)
from tuttitrip.accommodation.services.search_links_service import (
    PlatformNotAllowedError,
    SearchLinksUnavailableError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.jobs.api import JobQueueDep
from tuttitrip.shared.jobs.services.job_queue import JobQueueUnavailableError
from tuttitrip.shared.jobs.services.worker_liveness import WorkerUnavailableError
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripCoHost, TripHost, TripMember

router = APIRouter(prefix="/trips/{trip_id}/accommodation", tags=["accommodation"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "Trip not found, or the caller is not on it."}
}


@router.get(
    "/requirements",
    summary="Lodging requirements of the trip",
    responses=NOT_FOUND,
    dependencies=[requires(Feature.ACCOMMODATION, Access.READ)],
)
async def get_requirements(
    membership: TripMember, session: SessionDep
) -> RequirementsRead:
    """Read the lodging requirements (any member).

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The requirements and their version.
    """
    return await requirements_service.get_requirements(session, membership.trip_id)


@router.put(
    "/requirements",
    summary="Replace the lodging requirements of the trip",
    description=(
        "Replaces the whole set. Each requirement is an amenity, a platform or "
        "a maximum distance, hard or soft; they apply to the one lodging base of "
        "the whole trip. `version` moves only when the set really changes. An "
        "outing (a single day) has no lodging and gets 422."
    ),
    responses=NOT_FOUND,
    dependencies=[requires(Feature.ACCOMMODATION, Access.WRITE)],
)
async def put_requirements(
    data: RequirementsWrite, membership: TripCoHost, session: SessionDep
) -> RequirementsRead:
    """Replace the lodging requirements (co-host or host).

    Args:
        data: The whole new set.
        membership: The caller's membership (co-host or host).
        session: Database session.

    Returns:
        The stored requirements and the new version.
    """
    try:
        return await requirements_service.replace_requirements(
            session, membership, data
        )
    except RequirementsInvalidError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get(
    "/search-links",
    summary="Search links to lodging platforms with the trip's parameters",
    description=(
        "Dates, group (adults, children's ages), area and a nightly price ceiling "
        "from the budget. Nothing is fetched from the platforms: a person opens "
        "the link after the approval card and pastes offers back. Each parameter "
        "says whether the platform documents it; `fallback_url` has no filters. "
        "A hard platform requirement removes the other platforms. The result is "
        "a small fixed set (at most one link per platform), so it is not "
        "paginated. A trip without dates or a single-day outing gets 422."
    ),
    responses=NOT_FOUND,
    dependencies=[requires(Feature.ACCOMMODATION, Access.READ)],
)
async def get_search_links(
    membership: TripMember, session: SessionDep
) -> SearchLinksRead:
    """Build the search links (any member).

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The parameters and one link per allowed platform.
    """
    try:
        return await search_links_service.get_search_links(session, membership)
    except SearchLinksUnavailableError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post(
    "/search-links/opened",
    summary="Log that the host approved opening a platform search",
    description=(
        "Appends an entry (platform, link, parameters, author) to the log. The "
        "parameters are rebuilt on the server from the trip's current data."
    ),
    status_code=status.HTTP_201_CREATED,
    responses=NOT_FOUND,
    dependencies=[requires(Feature.ACCOMMODATION, Access.WRITE)],
)
async def post_search_opened(
    data: SearchOpenWrite, membership: TripHost, session: SessionDep
) -> SearchOpeningRead:
    """Record the approval (host only).

    Args:
        data: The approved platform.
        membership: The caller's membership (host).
        session: Database session.

    Returns:
        The log entry.
    """
    try:
        return await search_links_service.record_opening(
            session, membership, data.platform
        )
    except (SearchLinksUnavailableError, PlatformNotAllowedError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get(
    "/search-links/opened",
    summary="Log of approved platform searches",
    responses=NOT_FOUND,
    dependencies=[requires(Feature.ACCOMMODATION, Access.READ)],
)
async def list_search_openings(
    membership: TripMember,
    session: SessionDep,
    query: Annotated[OpeningQuery, Query()],
) -> Page[SearchOpeningRead]:
    """Read the log (any member), newest first by default.

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.
        query: Page, sort and filters.

    Returns:
        One page of entries.
    """
    return await search_links_service.list_openings(session, membership.trip_id, query)


UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    503: {"description": "The worker or the job queue is unavailable."}
}


@router.post(
    "/offers",
    summary="Check a pasted lodging offer against the requirements",
    description=(
        "Takes a pasted offer (`document_id` from `POST /planning/linter/trips/"
        "{trip_id}/documents`, kind `offer`), the nights it is for and optionally "
        "its link and the amenities the host confirmed by hand. Platform "
        "requirements are decided by the link's domain, so they never wait for "
        "the worker. Amenities without a host answer go to the worker job "
        "`extract_offer_evidence` (`job_id`); poll `GET .../offers/{offer_id}`. "
        "Every requirement is `met` or `unmet` with a quote, or `unconfirmed` with "
        "a `reason`; silence in the offer is `unconfirmed` (`no_mention`), never "
        "`unmet`. 422 for an outing, nights outside the trip or an unknown "
        "document; 503 while the worker is missing (only when a job is needed)."
    ),
    status_code=status.HTTP_202_ACCEPTED,
    responses={**NOT_FOUND, **UNAVAILABLE},
    dependencies=[requires(Feature.ACCOMMODATION, Access.WRITE)],
)
async def create_offer(
    data: OfferCreate, membership: TripCoHost, session: SessionDep, queue: JobQueueDep
) -> OfferRead:
    """Store an offer and start its check (co-host or host).

    Args:
        data: The offer.
        membership: The caller's membership (co-host or host).
        session: Database session.
        queue: Job queue.

    Returns:
        The offer with its check so far (`pending` while the worker runs).
    """
    try:
        return await offer_service.create_offer(session, queue, membership, data)
    except OfferInvalidError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except WorkerUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except JobQueueUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue unavailable"
        ) from exc


@router.get(
    "/offers/{offer_id}",
    summary="A pasted offer and its three-state check",
    description=(
        "Checks are computed on every read against the current requirements: "
        "`stale` says they changed since the offer was checked, and a "
        "requirement added later is `unconfirmed` (`not_checked`). `score` is "
        "S_h of E2 (met 1, unconfirmed 0.4, unmet 0)."
    ),
    responses={
        404: {"description": "Trip or offer not found, or the caller is not on it."},
        **UNAVAILABLE,
    },
    dependencies=[requires(Feature.ACCOMMODATION, Access.READ)],
)
async def get_offer(
    offer_id: UUID, membership: TripMember, session: SessionDep, queue: JobQueueDep
) -> OfferRead:
    """Read an offer with its check (any member).

    Args:
        offer_id: Offer id.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.
        queue: Job queue (read while the worker job runs).

    Returns:
        The offer and its check.
    """
    try:
        return await offer_service.get_offer(session, queue, membership, offer_id)
    except OfferNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Offer not found") from exc
    except JobQueueUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue unavailable"
        ) from exc
