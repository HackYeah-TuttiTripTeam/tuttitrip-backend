"""The preliminary plan during the interview ("Build the plan now").

The "Build the plan now" button and the agent tool both call ``build``, so they
cannot differ. The plan is computed by the planning service, which runs the
solver outside the event loop and stores a new version marked as a draft: it
never edits an earlier version.
"""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.interview import constants
from tuttitrip.interview.logic import plan_defaults
from tuttitrip.interview.logic.plan_defaults import MissingCityError
from tuttitrip.interview.schemas import DraftPlanRead
from tuttitrip.interview.services import session_service
from tuttitrip.planning.plans.schemas import (
    MISSING_CARD,
    MissingField,
    MissingInput,
    PlanMissingInputsDetail,
)
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.plans.services.plan_service import (
    CatalogEmptyError,
    MissingInputsError,
    PlanInputError,
)
from tuttitrip.trips.schemas import TripMembership

__all__ = [
    "MISSING_CITY",
    "CatalogEmptyError",
    "MissingCityError",
    "MissingInputsError",
    "PlanInputError",
    "build",
]

MISSING_CITY = PlanMissingInputsDetail(
    message=constants.MISSING_CITY_PL,
    missing=[
        MissingInput(
            field=MissingField.DESTINATION, kind=MISSING_CARD[MissingField.DESTINATION]
        )
    ],
)
"""The 422 body of a preliminary plan asked for before the trip has a city."""


async def build(
    session: AsyncSession, membership: TripMembership, today: date | None = None
) -> DraftPlanRead:
    """Compute a preliminary plan from what the interview knows so far.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        today: The calendar day the assumed date is counted from (tests); the
            server's day when omitted.

    Returns:
        The stored version and the assumptions made.

    Raises:
        MissingCityError: The trip has no city yet.
        PlanInputError: The data cannot be planned (e.g. a city outside the catalog).
    """
    known = await session_service.get_knowledge(session, membership)
    spec = plan_defaults.plan_defaults(
        known,
        today or date.today(),  # ruff: ignore[call-date-today] the server's own calendar day
    )
    plan, _created = await plan_service.generate_plan(
        session, membership, None, spec.assumptions
    )
    return DraftPlanRead(
        plan_id=plan.id,
        version=plan.version,
        plan_hash=plan.plan_hash,
        assumptions=spec.notes,
    )
