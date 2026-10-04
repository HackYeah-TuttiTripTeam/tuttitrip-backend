"""Plan endpoints (nested under a trip)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from tuttitrip.planning.plans.logic.sample_plan import Scenario, sample_plan
from tuttitrip.planning.plans.schemas import (
    PlanCreate,
    PlanRead,
    ReplanRead,
    ReplanRequest,
)
from tuttitrip.planning.plans.services import plan_service, replan_service
from tuttitrip.planning.plans.services.plan_service import (
    PlanInputError,
    PlanNotFoundError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripMember

router = APIRouter(prefix="/trips/{trip_id}/plans", tags=["planning"])

EXAMPLE_TRIP_ID = UUID("00000000-0000-4000-8000-000000000042")
NOT_FOUND = {404: {"description": "Trip not found, or the caller is not on it."}}


def plan_examples() -> dict[str, dict[str, object]]:
    """Named examples of the response shape: a group, one person, an approval.

    Returns:
        OpenAPI ``examples`` entries built from the fixed sample plans.
    """
    return {
        name: {
            "summary": summary,
            "value": sample_plan(EXAMPLE_TRIP_ID, scenario=scenario).model_dump(
                mode="json"
            ),
        }
        for name, summary, scenario in (
            ("group", "Group of four (section 7)", Scenario.GROUP),
            ("solo", "One person (n = 1): show domains, not Jain", Scenario.SOLO),
            ("needs_approval", "Over B_do: 1198, +98, kappa 18.7", Scenario.APPROVAL),
        )
    }


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Generate a plan with the fairness measure",
    description=(
        "Runs the algorithm of `docs/algorytm.md` (one solo run per person, then "
        "the group plan) and stores a new version. The same input (trip, people, "
        "preferences, ratings, vetoes, catalog, `alpha`, preset) returns the "
        "latest version with 200 and the same `plan_hash`; an input that went back to "
        "an older state gets a new version. Any member may ask (a member's veto "
        "triggers the recompute): the input is read with a host-level view, so the "
        "result does not depend on who asks. `explain` is limited to the caller's "
        "own cards below the co-host role, because the effort of a person depends "
        "on their health limits; the ledger (`u`, `r`, domains) is visible to all. "
        "The examples show the response shape."
    ),
    responses={
        **NOT_FOUND,
        200: {"model": PlanRead, "description": "Existing version for the same input."},
        201: {"content": {"application/json": {"examples": plan_examples()}}},
        403: {"description": "Missing the `planning.plans:WRITE` permission."},
        422: {"description": "The trip lacks dates, a city or people."},
    },
    dependencies=[requires(Feature.PLANNING_PLANS, Access.WRITE)],
)
async def create_plan(
    session: SessionDep,
    membership: TripMember,
    response: Response,
    data: PlanCreate | None = None,
) -> PlanRead:
    """Generate a plan.

    Args:
        session: Database session.
        membership: The caller's membership of ``{trip_id}``.
        response: Used to answer 200 for an existing version.
        data: Optional alpha and weight preset.

    Returns:
        The stored version.

    Raises:
        HTTPException: 422 when the trip cannot be planned yet.
    """
    try:
        plan, created = await plan_service.generate_plan(session, membership, data)
    except PlanInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return plan


@router.get(
    "/latest",
    summary="Latest plan of the trip",
    description=(
        "Returns 404 `No plan yet` when the trip has no plan (the empty state of "
        "the plan view)."
    ),
    responses={
        404: {"description": "No plan yet, trip not found or caller not on it."}
    },
    dependencies=[requires(Feature.PLANNING_PLANS, Access.READ)],
)
async def get_latest_plan(session: SessionDep, membership: TripMember) -> PlanRead:
    """Latest plan.

    Args:
        session: Database session.
        membership: The caller's membership of ``{trip_id}``.

    Returns:
        The newest version.

    Raises:
        HTTPException: 404 when there is no plan.
    """
    try:
        return await plan_service.latest_plan(session, membership)
    except PlanNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No plan yet") from exc


@router.get(
    "/{plan_id}",
    summary="One stored plan version",
    responses={404: {"description": "Plan, trip not found or caller not on it."}},
    dependencies=[requires(Feature.PLANNING_PLANS, Access.READ)],
)
async def get_plan(
    session: SessionDep, membership: TripMember, plan_id: UUID
) -> PlanRead:
    """One stored version.

    Args:
        session: Database session.
        membership: The caller's membership of ``{trip_id}``.
        plan_id: Version id.

    Returns:
        The version.

    Raises:
        HTTPException: 404 when the trip has no such version.
    """
    try:
        return await plan_service.get_plan(session, membership, plan_id)
    except PlanNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found") from exc


@router.post(
    "/{plan_id}/replan",
    summary="Replan the rest of a day (rain), from a moment on",
    description=(
        "Extension outside v1.0: replaces the rest of a day with the best plan under "
        "rain (`u_ip` times `0.3 + 0.7 * [indoor]`), by the same goal `J` and the "
        "same hard rules, penalising the number of changes and the shift of kept "
        "visits. Stops that started before `as_of` stay. Nothing is stored. A "
        "co-host's or host's replan is `active`; a member's that touches other "
        "people is `pending_host`. Weather is not fetched: rain is a person's "
        "decision."
    ),
    responses={
        **NOT_FOUND,
        422: {
            "description": "The day is not in the plan, or the trip cannot be planned."
        },
    },
    dependencies=[requires(Feature.PLANNING_PLANS, Access.WRITE)],
)
async def replan_day(
    session: SessionDep, membership: TripMember, plan_id: UUID, data: ReplanRequest
) -> ReplanRead:
    """Replan the rest of a day.

    Args:
        session: Database session.
        membership: The caller's membership of ``{trip_id}``.
        plan_id: The version to start from.
        data: Context, day and ``as_of``.

    Returns:
        The day after the replan and what changed.

    Raises:
        HTTPException: 404 for an unknown version, 422 for an impossible request.
    """
    try:
        return await replan_service.replan(session, membership, plan_id, data)
    except PlanNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found") from exc
    except PlanInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
