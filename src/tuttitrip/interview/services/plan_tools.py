"""The interview tool "Build the plan now" (issue #60).

It calls the same service as the button (``draft_plan_service``). A turn builds
at most one plan, so a chatty model cannot flood the solver; asking again in the
same turn returns the plan already built.
"""

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from tuttitrip.interview.services import draft_plan_service
from tuttitrip.interview.services.draft_plan_service import (
    MissingCityError,
    PlanInputError,
)
from tuttitrip.interview.services.interview_deps import InterviewDeps
from tuttitrip.interview.services.tool_support import snapshot, tool_session

plan_toolset = FunctionToolset[InterviewDeps]()

NOT_BUILT_NO_CITY = (
    "NOT BUILT: the trip has no city yet. Ask the organizer for the city (a "
    "show_card with field destination), then build the plan."
)
NOT_BUILT = "NOT BUILT: the plan cannot be made yet: {reason}"


@plan_toolset.tool
async def build_plan_now(ctx: RunContext[InterviewDeps]) -> ToolReturn | str:
    """Build a preliminary plan from what is known so far.

    Use it when the organizer asks to see the plan ("Zbuduj plan", "pokaż plan")
    at any point of the interview. Missing data is filled with assumptions that
    the result lists; say them to the organizer in one sentence and carry on
    with the interview. Needs only the city.

    Args:
        ctx: The run context.

    Returns:
        The plan version and the assumptions, with a fresh snapshot; or a
        NOT BUILT message when the city is missing.
    """
    state = ctx.deps.state
    if state.draft_plan is None:
        async with tool_session(ctx) as session:
            try:
                state.draft_plan = await draft_plan_service.build(
                    session, ctx.deps.membership
                )
            except MissingCityError:
                return NOT_BUILT_NO_CITY
            except PlanInputError as exc:
                return NOT_BUILT.format(reason=exc)
    async with tool_session(ctx) as session:
        return await snapshot(ctx, session, state.draft_plan.model_dump(mode="json"))
