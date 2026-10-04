"""Accommodation endpoints (nested under a trip)."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from tuttitrip.accommodation.schemas import (
    OpeningQuery,
    RequirementsRead,
    RequirementsWrite,
    SearchLinksRead,
    SearchOpeningRead,
    SearchOpenWrite,
)
from tuttitrip.accommodation.services import requirements_service, search_links_service
from tuttitrip.accommodation.services.requirements_service import (
    RequirementsInvalidError,
)
from tuttitrip.accommodation.services.search_links_service import (
    PlatformNotAllowedError,
    SearchLinksUnavailableError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripCoHost, TripHost, TripMember

router = APIRouter(prefix="/trips/{trip_id}/accommodation", tags=["accommodation"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "description": "Trip not found, or the caller is not on it."
    }
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
