"""The daily "anyway" suggestion (nested under a plan version)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from tuttitrip.planning.anyway.schemas import AnywayRead
from tuttitrip.planning.anyway.services import anyway_service
from tuttitrip.planning.anyway.services.anyway_service import AnywayNotFoundError
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.jobs.api import JobQueueDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripHost, TripMember

router = APIRouter(prefix="/trips/{trip_id}/plans/{plan_id}/anyway", tags=["planning"])

NOT_FOUND: dict[int | str, dict[str, object]] = {
    404: {"description": "Plan, suggestion, trip not found or caller not on it."}
}


@router.get(
    "",
    summary='The "anyway" suggestions of a plan version',
    description=(
        "At most one suggestion per day: an iconic place or a unique experience "
        "that fits the group less, with the cost of adding it (change of `min r`, "
        "cost and time) and a justification. The justification is a template "
        "until the model's text is written (`justification_pending`); the same "
        "data gives the same suggestion. A rejected suggestion is not listed. "
        "Accepting is the existing `must` override on the place."
    ),
    responses=NOT_FOUND,
    dependencies=[requires(Feature.PLANNING_PLANS, Access.READ)],
)
async def get_anyway(
    session: SessionDep, membership: TripMember, queue: JobQueueDep, plan_id: UUID
) -> AnywayRead:
    """The suggestions.

    Args:
        session: Database session.
        membership: The caller's membership of ``{trip_id}``.
        queue: Job queue (the model's text is read from it).
        plan_id: Plan version id.

    Returns:
        The suggestions.

    Raises:
        HTTPException: 404 when the trip has no such version.
    """
    try:
        return await anyway_service.read(session, membership, queue, plan_id)
    except AnywayNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found") from exc


@router.post(
    "/{place_id}/reject",
    summary="Reject the suggestion of a day",
    description=(
        "The host does not want the place. It does not come back on that day in "
        "this or any later version of the plan. Only the host decides."
    ),
    responses=NOT_FOUND,
    dependencies=[requires(Feature.PLANNING_PLANS, Access.WRITE)],
)
async def reject_anyway(
    session: SessionDep,
    membership: TripHost,
    queue: JobQueueDep,
    plan_id: UUID,
    place_id: UUID,
) -> AnywayRead:
    """Reject a suggestion.

    Args:
        session: Database session.
        membership: The host's membership of ``{trip_id}``.
        queue: Job queue.
        plan_id: Plan version id.
        place_id: The suggested place.

    Returns:
        The suggestions that remain.

    Raises:
        HTTPException: 404 when the version has no such suggestion.
    """
    try:
        return await anyway_service.reject(
            session, membership, queue, plan_id, place_id
        )
    except AnywayNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion not found") from exc
