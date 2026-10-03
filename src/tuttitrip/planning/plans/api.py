"""Plan endpoints (nested under a trip). STUB: fixed response until #50."""

from fastapi import APIRouter, status

from tuttitrip.planning.plans.schemas import PlanCreate, PlanRead
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripMember

router = APIRouter(prefix="/trips/{trip_id}/plans", tags=["planning"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[requires(Feature.PLANNING_PLANS, Access.WRITE)],
)
async def create_plan(
    membership: TripMember, data: PlanCreate | None = None
) -> PlanRead:
    """Generate a plan with the fairness measure, ledger and verdicts (stub).

    Stub: returns a fixed sample plan (numbers of section 7 of
    ``docs/algorytm.md``); the real solver arrives with backend#50. The shape
    will not change.

    Args:
        membership: The caller's membership of ``{trip_id}``.
        data: Optional alpha and weight preset.

    Returns:
        The plan.
    """
    return plan_service.generate_plan(membership, data)


@router.get("/latest", dependencies=[requires(Feature.PLANNING_PLANS, Access.READ)])
async def get_latest_plan(membership: TripMember) -> PlanRead:
    """Latest plan of the trip (stub: the same fixed sample plan).

    Args:
        membership: The caller's membership of ``{trip_id}``.

    Returns:
        The plan.
    """
    return plan_service.latest_plan(membership)
