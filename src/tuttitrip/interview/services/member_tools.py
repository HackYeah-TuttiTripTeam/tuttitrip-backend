"""Interview tools of a trip member (issue #91): their own interests and limits.

Same agent and endpoint as the host's interview, a different set of tools,
chosen by the member's role (``tool_support.for_member``). Not one of them takes
a person: the profile is ``deps.own_profile_id``, found from the member's
account on the server, so nothing said in the conversation can point a write at
someone else. There is no tool for the budget, dates, people or anybody else's
preferences; the assistant tells the member that the organizer does that.
"""

from typing import Annotated
from uuid import UUID

from pydantic import Field
from pydantic_ai import ModelRetry, RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from tuttitrip.interview import constants
from tuttitrip.interview.schemas import ConstraintKind
from tuttitrip.interview.services import preference_tools
from tuttitrip.interview.services.interview_deps import InterviewDeps
from tuttitrip.places.schemas import DietTag, PlaceTag
from tuttitrip.profiles.preferences.schemas import ImportanceDomain, PoolPoints

member_toolset = FunctionToolset[InterviewDeps]()

NO_PROFILE = "This member has no profile on the trip."


def own(ctx: RunContext[InterviewDeps]) -> UUID:
    """The member's own profile id, from the server.

    Args:
        ctx: The run context.

    Returns:
        The profile id.

    Raises:
        ModelRetry: Not a member's interview (cannot happen: the tools are only
            offered there).
    """
    if ctx.deps.own_profile_id is None:
        raise ModelRetry(NO_PROFILE)
    return ctx.deps.own_profile_id


@member_toolset.tool
async def add_my_interest(
    ctx: RunContext[InterviewDeps],
    interest: PlaceTag,
    strength: Annotated[float, Field(ge=0, le=1)] = 1.0,
) -> ToolReturn | str:
    """Save an interest of the member ("lubię muzea techniki" is science).

    Args:
        ctx: The run context.
        interest: Which interest.
        strength: How much, from 0 to 1.

    Returns:
        The saved interest and a fresh snapshot.
    """
    return await preference_tools.add_interest(ctx, own(ctx), interest, strength)


@member_toolset.tool
async def set_my_diet(
    ctx: RunContext[InterviewDeps], diet: DietTag, *, enabled: bool = True
) -> ToolReturn | str:
    """Save a diet of the member.

    Args:
        ctx: The run context.
        diet: Which diet.
        enabled: True to add it, False to remove it.

    Returns:
        The saved diet and a fresh snapshot.
    """
    return await preference_tools.set_diet(ctx, own(ctx), diet, enabled=enabled)


@member_toolset.tool
async def set_my_constraint(
    ctx: RunContext[InterviewDeps], kind: ConstraintKind, *, value: bool = True
) -> ToolReturn | str:
    """Save an access limit of the member (stairs, wheelchair, heat, cold).

    Args:
        ctx: The run context.
        kind: Which limit.
        value: True to set it, False to clear it.

    Returns:
        The saved limit and a fresh snapshot.
    """
    return await preference_tools.set_constraint(ctx, own(ctx), kind, value=value)


@member_toolset.tool
async def set_my_importance_points(
    ctx: RunContext[InterviewDeps], domain: ImportanceDomain, points: PoolPoints
) -> ToolReturn | str:
    """Give a domain points from the member's own pool of ten.

    Args:
        ctx: The run context.
        domain: Lodging, food, attractions, pace or cost.
        points: Points for this domain, 0 to ten.

    Returns:
        The whole pool after the change and a fresh snapshot.
    """
    return await preference_tools.set_importance_points(ctx, own(ctx), domain, points)


@member_toolset.tool
async def classify_my_diet(
    ctx: RunContext[InterviewDeps],
    text: Annotated[str, Field(min_length=1, max_length=constants.MAX_FREE_TEXT)],
) -> ToolReturn | str:
    """Let a decision model read a free sentence about food as a diet and save it.

    Args:
        ctx: The run context.
        text: The member's words, e.g. "nie jem mięsa".

    Returns:
        The saved diet and a snapshot, or a request to confirm.
    """
    return await preference_tools.classify_diet(ctx, own(ctx), text)


@member_toolset.tool
async def classify_my_constraint(
    ctx: RunContext[InterviewDeps],
    text: Annotated[str, Field(min_length=1, max_length=constants.MAX_FREE_TEXT)],
) -> ToolReturn | str:
    """Let a decision model read a free sentence as an access limit and save it.

    Args:
        ctx: The run context.
        text: The member's words, e.g. "nie wchodzę po schodach".

    Returns:
        The saved limit and a snapshot, or a request to confirm.
    """
    return await preference_tools.classify_constraint(ctx, own(ctx), text)
