"""Plan endpoints (nested under a trip). STUB: fixed response until #50."""

from fastapi import APIRouter, status

from tuttitrip.planning.plans.schemas import PlanCreate, PlanRead
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripMember

router = APIRouter(prefix="/trips/{trip_id}/plans", tags=["planning"])

STUB = {"x-stub": True}
STUB_NOTE = (
    "STUB: until backend#50 the content is a fixed sample (section 7 of "
    "`docs/algorytm.md`); the shape is final."
)
NOT_FOUND = {404: {"description": "Trip not found, or the caller is not on it."}}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Generate a plan with the fairness measure (STUB)",
    description=(
        f"{STUB_NOTE}\n\nGenerates a plan with the fairness measure, ledger, "
        "verdicts and budget. Repeating the call with the same `input_hash` "
        "returns 200 with the existing version instead of 201 (the stub "
        "always returns 201)."
    ),
    responses={
        **NOT_FOUND,
        200: {"model": PlanRead, "description": "Existing version for the same input."},
        201: {
            "content": {
                "application/json": {"examples": plan_service.openapi_examples()}
            }
        },
    },
    openapi_extra=STUB,
    dependencies=[requires(Feature.PLANNING_PLANS, Access.WRITE)],
)
async def create_plan(
    membership: TripMember, data: PlanCreate | None = None
) -> PlanRead:
    """Generate a plan (stub).

    Args:
        membership: The caller's membership of ``{trip_id}``.
        data: Optional alpha and weight preset.

    Returns:
        The plan.
    """
    return plan_service.generate_plan(membership, data)


@router.get(
    "/latest",
    summary="Latest plan of the trip (STUB)",
    description=(
        f"{STUB_NOTE}\n\nReturns 404 `No plan yet` when the trip has no plan "
        "(the empty state of the plan view); the stub always has one and never "
        "returns it."
    ),
    responses={
        404: {"description": "No plan yet, trip not found or caller not on it."}
    },
    openapi_extra=STUB,
    dependencies=[requires(Feature.PLANNING_PLANS, Access.READ)],
)
async def get_latest_plan(membership: TripMember) -> PlanRead:
    """Latest plan (stub).

    Args:
        membership: The caller's membership of ``{trip_id}``.

    Returns:
        The plan.
    """
    return plan_service.latest_plan(membership)
