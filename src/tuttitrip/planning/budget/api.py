"""Budget endpoints (nested under a trip): the daily budget and the proposal."""

from fastapi import APIRouter, HTTPException, Response, status

from tuttitrip.planning.budget.schemas import BudgetDaysRead, ProposalOutcome
from tuttitrip.planning.budget.services import budget_service
from tuttitrip.planning.budget.services.budget_service import BudgetInputError
from tuttitrip.planning.plans.services.plan_service import PlanNotFoundError
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripCoHost, TripMember

router = APIRouter(prefix="/trips/{trip_id}/budget", tags=["planning"])

NOT_FOUND = {404: {"description": "Trip not found, or the caller is not on it."}}


@router.get(
    "/days",
    summary="Budget of every day, what was spent and what is left",
    description=(
        "For each day of the trip: the budget `from`/`to` (the trip's own day range, "
        "else the trip budget divided by the days), `B_max` with the trip's margin, "
        "the group's expenses that count against it (food, transport, activities and "
        "uncategorised ones; shopping, lodging and other are `outside_plan`) and what "
        "is left. A day is `over_budget` above `B_do`. `proposal` is the stored "
        "cheaper plan for the days after the last overrun, if one was made. The "
        "days are bounded by the trip's length, so they are not paginated."
    ),
    responses={
        **NOT_FOUND,
        422: {"description": "The trip has no dates or no budget."},
    },
    dependencies=[requires(Feature.PLANNING_PLANS, Access.READ)],
)
async def get_budget_days(
    session: SessionDep, membership: TripMember
) -> BudgetDaysRead:
    """Daily budget.

    Args:
        session: Database session.
        membership: The caller's membership of ``{trip_id}``.

    Returns:
        The days and the proposal, if any.

    Raises:
        HTTPException: 422 when the trip cannot be budgeted yet.
    """
    try:
        return await budget_service.get_days(session, membership)
    except BudgetInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post(
    "/proposal",
    status_code=status.HTTP_201_CREATED,
    summary="Propose cheaper days after an overrun",
    description=(
        "When a day went over its budget and the trip's setting "
        "`propose_cheaper_alternatives` is on, plans the days after the last "
        "overrun with the budget that is left (B_od and B_do minus everything "
        "spent) by the same solver and goal as any plan, and stores the result as "
        "an alternative of the latest plan version (read it with "
        "`GET /trips/{trip_id}/plans/{plan_id}`). It changes the plan only when "
        "the host approves. A proposal that needs going over `B_do` goes through "
        "the consent of E6 (`needs_approval`, `kappa`). The same expenses and data "
        "return the same proposal with 200; with nothing to propose the answer is "
        "200 and `skipped` says why."
    ),
    responses={
        **NOT_FOUND,
        200: {
            "model": ProposalOutcome,
            "description": "Existing, or nothing to propose.",
        },
        404: {"description": "No plan yet, trip not found or caller not on it."},
        422: {"description": "No dates or budget, or the plan is outdated."},
    },
    dependencies=[requires(Feature.PLANNING_PLANS, Access.WRITE)],
)
async def propose_cheaper_days(
    session: SessionDep, membership: TripCoHost, response: Response
) -> ProposalOutcome:
    """Compute and store a proposal.

    Args:
        session: Database session.
        membership: The caller's membership of ``{trip_id}`` (co-host or host).
        response: Used to answer 200 when nothing new was stored.

    Returns:
        The outcome.

    Raises:
        HTTPException: 404 without a plan, 422 when the trip cannot be budgeted.
    """
    try:
        outcome = await budget_service.propose(session, membership)
    except PlanNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No plan yet") from exc
    except BudgetInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return outcome
