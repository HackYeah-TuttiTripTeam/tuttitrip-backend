"""Linter endpoints of a trip."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status

from tuttitrip.planning.linter.schemas import LintReport
from tuttitrip.planning.plans.logic.input_builder import PlanInputError
from tuttitrip.planning.plans.services.plan_service import (
    CatalogMissingError,
    PlanNotFoundError,
)
from tuttitrip.planning.trip_linter.schemas import (
    ItemPick,
    PasteAccepted,
    PasteCheckRead,
    PasteCreate,
)
from tuttitrip.planning.trip_linter.services import trip_lint_service
from tuttitrip.planning.trip_linter.services.trip_lint_service import (
    PasteInputError,
    PasteNotFoundError,
    PasteNotReadyError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.jobs.api import JobQueueDep
from tuttitrip.shared.jobs.services.job_queue import JobQueueUnavailableError
from tuttitrip.shared.jobs.services.worker_liveness import WorkerUnavailableError
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripCoHost, TripMember

router = APIRouter(prefix="/trips/{trip_id}/linter", tags=["planning"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "Trip or check not found, or the caller is not on the trip."}
}
UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    503: {"description": "The worker or the job queue is unavailable."}
}
CANNOT_LINT: dict[int | str, dict[str, Any]] = {
    409: {"description": "The trip's city has no places in the catalog."},
    422: {"description": "The trip lacks dates, a city or people."},
}
PasteId = Annotated[UUID, Path(description="The id returned by the 202.")]


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


@router.post(
    "/plans/{plan_id}",
    summary="Lint a version of our plan",
    description=(
        "Synchronous, no worker: the stored version goes through every rule. "
        "`count` is the number of violations, each rule appears with its count "
        "(zeros too). The solver's plan of the demo family has 0."
    ),
    responses={**NOT_FOUND, **CANNOT_LINT},
    dependencies=[requires(Feature.PLANNING_LINTER, Access.READ)],
)
async def lint_plan(
    membership: TripMember,
    session: SessionDep,
    queue: JobQueueDep,
    plan_id: UUID,
) -> LintReport:
    """Lint a stored plan version.

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.
        queue: Job queue (the plan read).
        plan_id: The version.

    Returns:
        The report.

    Raises:
        HTTPException: 404 for an unknown version, 409 without a catalog, 422
            for a trip that cannot be linted.
    """
    try:
        return await trip_lint_service.check_plan(session, queue, membership, plan_id)
    except PlanNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found") from exc
    except CatalogMissingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "catalog_missing") from exc
    except PlanInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post(
    "/pastes",
    summary="Check a plan pasted from another tool",
    description=(
        "Stores the text, then queues the worker's parse. 202 with the `paste_id` "
        "to poll. A missing worker answers 503 and the text stays stored."
    ),
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **NOT_FOUND,
        **UNAVAILABLE,
        422: {"description": "Empty or too long text, or the trip has no city/dates."},
    },
    dependencies=[requires(Feature.PLANNING_LINTER, Access.WRITE)],
)
async def create_paste(
    membership: TripCoHost,
    session: SessionDep,
    queue: JobQueueDep,
    data: PasteCreate,
) -> PasteAccepted:
    """Store a pasted plan and queue its parse.

    Args:
        membership: The caller's membership of ``{trip_id}`` (co-host or host).
        session: Database session.
        queue: Job queue.
        data: The text.

    Returns:
        The id to poll and the job.

    Raises:
        HTTPException: 422 when the trip has no city or dates, 503 without a worker.
    """
    try:
        return await trip_lint_service.create_paste(session, queue, membership, data)
    except PasteInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except (WorkerUnavailableError, JobQueueUnavailableError) as exc:
        raise _unavailable(exc) from exc


@router.get(
    "/pastes/{paste_id}",
    summary="Report of a pasted plan",
    description=(
        "`pending` while the worker parses; then `done` with the number of "
        "violations, every rule with its count and the items that were not "
        "recognised, or `failed` with the worker's `error_code`. The report is "
        "stored at the first read after the job and stays the same until a pick."
    ),
    responses={**NOT_FOUND, **UNAVAILABLE, **CANNOT_LINT},
    dependencies=[requires(Feature.PLANNING_LINTER, Access.READ)],
)
async def get_paste(
    membership: TripMember,
    session: SessionDep,
    queue: JobQueueDep,
    paste_id: PasteId,
) -> PasteCheckRead:
    """Read the check of a pasted plan.

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.
        queue: Job queue.
        paste_id: The check.

    Returns:
        State and, when done, the report.

    Raises:
        HTTPException: 404, 409 without a catalog, 422 for a trip that cannot be
            linted, 503 when the queue is down.
    """
    try:
        return await trip_lint_service.get_paste(session, queue, membership, paste_id)
    except PasteNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paste not found") from exc
    except CatalogMissingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "catalog_missing") from exc
    except PlanInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except JobQueueUnavailableError as exc:
        raise _unavailable(exc) from exc


@router.patch(
    "/pastes/{paste_id}/items/{index}",
    summary="Pick the catalog place of an unsure item",
    description=(
        "The host chooses one of the item's `candidates`; the report is "
        "recomputed with that place checked by every rule."
    ),
    responses={
        **NOT_FOUND,
        **UNAVAILABLE,
        **CANNOT_LINT,
        409: {"description": "The text is not parsed yet, or the city has no places."},
        422: {"description": "The place is not a candidate of the item."},
    },
    dependencies=[requires(Feature.PLANNING_LINTER, Access.WRITE)],
)
async def pick_item(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] path, body and dependencies
    membership: TripCoHost,
    session: SessionDep,
    queue: JobQueueDep,
    paste_id: PasteId,
    index: Annotated[int, Path(ge=0, description="Item index in the text.")],
    data: ItemPick,
) -> PasteCheckRead:
    """Choose the place of an item.

    Args:
        membership: The caller's membership of ``{trip_id}`` (co-host or host).
        session: Database session.
        queue: Job queue.
        paste_id: The check.
        index: The item.
        data: The chosen place.

    Returns:
        The check with the recomputed report.

    Raises:
        HTTPException: 404, 409 while not parsed, 422 for a foreign place.
    """
    try:
        return await trip_lint_service.pick_item(
            session, queue, membership, paste_id, index, data.place_id
        )
    except PasteNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paste not found") from exc
    except PasteNotReadyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except PasteInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except CatalogMissingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "catalog_missing") from exc
    except PlanInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except JobQueueUnavailableError as exc:
        raise _unavailable(exc) from exc
