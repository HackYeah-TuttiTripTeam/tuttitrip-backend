"""Linter endpoints."""

from fastapi import APIRouter, status

from tuttitrip.planning.linter.schemas import (
    DocumentCreate,
    DocumentRead,
    LintReport,
    LintRequest,
)
from tuttitrip.planning.linter.services import document_service, linter_service
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripCoHost

router = APIRouter(prefix="/planning/linter", tags=["planning"])


@router.post("/check", dependencies=[requires(Feature.PLANNING_LINTER, Access.READ)])
def check(request: LintRequest) -> LintReport:
    """Lint a plan.

    Args:
        request: Plan and budget.

    Returns:
        All violations.
    """
    return linter_service.check_plan(request)


@router.post(
    # ruff: ignore[fast-api-unused-path-parameter] trip_id is read by TripCoHost
    "/trips/{trip_id}/documents",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Trip not found, or the caller is not on it."},
        422: {"description": "Empty text or longer than 20 000 characters."},
    },
    dependencies=[requires(Feature.PLANNING_LINTER, Access.WRITE)],
)
async def create_document(
    membership: TripCoHost, data: DocumentCreate, session: SessionDep
) -> DocumentRead:
    """Save a plan or lodging offer pasted by the host.

    The worker reads the text by the returned ``id``; it is deleted with the trip.

    Args:
        membership: The caller's membership of ``{trip_id}`` (co-host or host).
        data: Kind and text.
        session: Database session.

    Returns:
        The stored document.
    """
    return await document_service.create_document(session, membership, data)
