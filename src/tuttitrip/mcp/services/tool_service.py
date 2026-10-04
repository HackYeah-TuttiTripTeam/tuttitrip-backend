"""What MCP tools do: thin calls into the services of other domains."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import NamedTuple
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.mcp import schemas as constants
from tuttitrip.mcp.schemas import McpFairness, McpPlan, VetoResult, WhoAmI
from tuttitrip.planning.linter.schemas import LintReport, NamedPlan
from tuttitrip.planning.linter.services import trip_lint_service
from tuttitrip.planning.plans.schemas import PlanRead
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.plans.services.plan_service import (
    PlanInputError,
    PlanNotFoundError,
)
from tuttitrip.profiles.feedback.schemas import (
    RatingRead,
    RatingUpdate,
    RatingValue,
    ReasonCode,
    VetoCreate,
)
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.profiles.feedback.services.feedback_service import (
    FeedbackForbiddenError,
    FeedbackPlaceNotFoundError,
    ProfileNotFoundError,
    VetoExistsError,
)
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import get_sessionmaker
from tuttitrip.shared.pagination.schemas import Page, PageParams
from tuttitrip.shared.permissions.logic.resolution import (
    EffectivePermissions,
    Grant,
    resolve,
)
from tuttitrip.shared.permissions.services import permission_service
from tuttitrip.shared.rate_limit.limiter import RateLimiter
from tuttitrip.trips.schemas import (
    TripListQuery,
    TripMembership,
    TripRead,
    TripRole,
)
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError, TripRoleError

log = logging.getLogger(__name__)


class ToolFailedError(Exception):
    """A tool refused; the message is safe to show to the client (no details)."""


class ToolCall(NamedTuple):
    """What a tool body needs: an open session and the caller."""

    session: AsyncSession
    user: AuthenticatedUser


@lru_cache(maxsize=1)
def write_limiter() -> RateLimiter:
    """The per-user limiter of the tools that change data.

    Returns:
        The process-wide limiter (``mcp.write_calls_per_minute``).
    """
    return RateLimiter(get_settings().mcp.write_calls_per_minute)


def record(
    tool: str, user: AuthenticatedUser, trip_id: UUID | None, outcome: str
) -> None:
    """Log one tool call: who, which tool, which trip, how it ended.

    Arguments of the call are never logged (they can carry pasted plans).

    Args:
        tool: Tool name.
        user: The caller.
        trip_id: The trip, if the tool has one.
        outcome: ``ok`` or the failure reason.
    """
    log.info("mcp tool=%s sub=%s trip=%s outcome=%s", tool, user.sub, trip_id, outcome)


def allow_write(user: AuthenticatedUser) -> None:
    """Count one write call of the user in the current minute.

    Args:
        user: The caller.

    Raises:
        ToolFailedError: When the user already used the minute's calls.
    """
    if not write_limiter().allow(user.sub):
        raise ToolFailedError(constants.RATE_LIMITED)


@asynccontextmanager
async def open_session() -> AsyncGenerator[AsyncSession]:
    """Open a database session for one tool call or permission lookup.

    Yields:
        An async session, closed afterwards.
    """
    async with get_sessionmaker()() as session:
        yield session


@asynccontextmanager
async def tool_call(
    name: str, user: AuthenticatedUser, trip_id: UUID | None
) -> AsyncGenerator[ToolCall]:
    """Session for one tool call; the call is logged either way.

    Args:
        name: Tool name.
        user: The caller.
        trip_id: The trip the tool works on, if any.

    Yields:
        The session and the caller.

    Raises:
        ToolFailedError: The tool refused (logged as ``refused``).
    """
    try:
        async with open_session() as session:
            yield ToolCall(session, user)
    except ToolFailedError:
        record(name, user, trip_id, "refused")
        raise
    record(name, user, trip_id, "ok")


async def load_permissions(
    session: AsyncSession, user: AuthenticatedUser
) -> EffectivePermissions:
    """Resolve the caller's permissions (the Auth0 admin claim skips the query).

    Args:
        session: Open session.
        user: The caller.

    Returns:
        Effective permissions, never read from the token itself; none at all
        for a blocked or deleted account.
    """
    grants: list[Grant] = []
    if not user.is_admin:
        loaded, blocked = await permission_service.load_access(session, user.sub)
        grants = [] if blocked else loaded  # a blocked account holds nothing
    return resolve(grants, superadmin=user.is_admin)


def whoami(user: AuthenticatedUser, permissions: EffectivePermissions) -> WhoAmI:
    """Describe the caller.

    Args:
        user: The caller.
        permissions: Their effective permissions.

    Returns:
        Identity and the permission map.
    """
    return WhoAmI(
        sub=user.sub,
        roles=user.roles,
        is_admin=user.is_admin,
        access=permissions.as_dict(),
    )


async def list_trips(
    session: AsyncSession, user: AuthenticatedUser, params: PageParams
) -> Page[TripRead]:
    """One page of the caller's trips, newest first.

    Args:
        session: Open session.
        user: The caller.
        params: Page and size.

    Returns:
        The page with the total.
    """
    query = TripListQuery(page=params.page, size=params.size)
    return await trip_service.list_trips(session, user.sub, query)


async def get_trip(
    session: AsyncSession, user: AuthenticatedUser, trip_id: UUID
) -> TripRead:
    """Read one trip the caller is a member of.

    Args:
        session: Open session.
        user: The caller.
        trip_id: Trip id.

    Returns:
        The trip with the caller's role.

    Raises:
        TripNotFoundError: Unknown trip or the caller is not on it.
    """
    membership = await trip_service.get_membership(
        session, trip_id, user.sub, TripRole.MEMBER
    )
    return await trip_service.get_trip(session, membership)


async def _member(
    session: AsyncSession, user: AuthenticatedUser, trip_id: UUID
) -> TripMembership:
    """Membership of the caller; unknown trip and a missing role look the same.

    Args:
        session: Open session.
        user: The caller.
        trip_id: Trip id.

    Returns:
        The caller's membership (any role).

    Raises:
        ToolFailedError: The trip is unknown or the caller is not on it.
    """
    try:
        return await trip_service.get_membership(
            session, trip_id, user.sub, TripRole.MEMBER
        )
    except (TripNotFoundError, TripRoleError) as exc:
        raise ToolFailedError(constants.TRIP_NOT_FOUND) from exc


async def _latest(session: AsyncSession, membership: TripMembership) -> PlanRead:
    try:
        return await plan_service.latest_plan(session, membership)
    except PlanNotFoundError as exc:
        raise ToolFailedError(constants.NO_PLAN) from exc


async def get_plan(
    session: AsyncSession, user: AuthenticatedUser, trip_id: UUID, day: int | None
) -> McpPlan:
    """The newest plan, day by day.

    Args:
        session: Open session.
        user: The caller.
        trip_id: Trip id.
        day: 1-based day, or None for every day.

    Returns:
        The plan with the requested day(s).

    Raises:
        ToolFailedError: No access, no plan, or no such day.
    """
    plan = await _latest(session, await _member(session, user, trip_id))
    days = plan.days if day is None else [d for d in plan.days if d.index == day]
    if not days:
        raise ToolFailedError(constants.DAY_OUT_OF_RANGE)
    return McpPlan(
        trip_id=plan.trip_id,
        version=plan.version,
        plan_hash=plan.plan_hash,
        created_at=plan.created_at,
        day_count=len(plan.days),
        days=days,
        lodging=plan.lodging,
        budget=plan.budget,
        conflicts=plan.conflicts,
    )


async def get_fairness(
    session: AsyncSession, user: AuthenticatedUser, trip_id: UUID
) -> McpFairness:
    """The fairness ledger of the newest plan (the numbers of the REST plan).

    Args:
        session: Open session.
        user: The caller.
        trip_id: Trip id.

    Returns:
        ``min r``, Jain, per person ``u``, ``u*``, ``r``, domains, misses, conflicts.

    Raises:
        ToolFailedError: No access or no plan.
    """
    plan = await _latest(session, await _member(session, user, trip_id))
    return McpFairness(
        trip_id=plan.trip_id,
        version=plan.version,
        plan_hash=plan.plan_hash,
        fairness=plan.fairness,
        floors_missed=plan.floors_missed,
        conflicts=plan.conflicts,
    )


async def get_violations(
    session: AsyncSession, user: AuthenticatedUser, trip_id: UUID
) -> LintReport:
    """The linter's report on the newest plan.

    Args:
        session: Open session.
        user: The caller.
        trip_id: Trip id.

    Returns:
        Every rule with its violations and warnings.

    Raises:
        ToolFailedError: No access, no plan, or the trip cannot be planned.
    """
    membership = await _member(session, user, trip_id)
    try:
        return await trip_lint_service.lint_latest_plan(session, membership)
    except PlanNotFoundError as exc:
        raise ToolFailedError(constants.NO_PLAN) from exc
    except PlanInputError as exc:
        raise ToolFailedError(constants.PLAN_NOT_POSSIBLE) from exc


async def lint_plan(
    session: AsyncSession, user: AuthenticatedUser, trip_id: UUID, plan: NamedPlan
) -> LintReport:
    """Check a plan from another tool against the trip's people and catalog.

    Args:
        session: Open session.
        user: The caller.
        trip_id: Trip id.
        plan: Days of named stops with times.

    Returns:
        Every rule with its violations and warnings.

    Raises:
        ToolFailedError: No access or the trip cannot be planned.
    """
    membership = await _member(session, user, trip_id)
    try:
        return await trip_lint_service.lint_named_plan(session, membership, plan)
    except PlanInputError as exc:
        raise ToolFailedError(constants.PLAN_NOT_POSSIBLE) from exc


async def _profile_of(
    session: AsyncSession, membership: TripMembership, on_behalf_of: UUID | None
) -> UUID:
    if on_behalf_of is not None:
        return on_behalf_of
    own = await profile_service.find_account_profile(
        session, membership.trip_id, membership.sub
    )
    if own is None:
        raise ToolFailedError(constants.PROFILE_NOT_FOUND)
    return own


async def rate_place(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] the arguments of the tool
    session: AsyncSession,
    user: AuthenticatedUser,
    trip_id: UUID,
    place_id: UUID,
    rating: RatingValue,
    reason: ReasonCode | None,
) -> RatingRead:
    """Set the caller's rating of a catalog place (repeating it changes nothing).

    Args:
        session: Open session.
        user: The caller.
        trip_id: Trip id.
        place_id: Catalog place.
        rating: want, neutral or dont_want.
        reason: Required for dont_want, forbidden otherwise.

    Returns:
        The stored rating.

    Raises:
        ToolFailedError: No access, no profile, unknown place, or the rate limit.
    """
    membership = await _member(session, user, trip_id)
    allow_write(user)
    profile_id = await _profile_of(session, membership, None)
    try:
        return await feedback_service.rate_place(
            session,
            membership,
            profile_id,
            place_id,
            RatingUpdate(value=rating, reason_code=reason),
        )
    except FeedbackPlaceNotFoundError as exc:
        raise ToolFailedError(constants.PLACE_NOT_FOUND) from exc


def _place_names(plan: PlanRead | None) -> dict[UUID, str]:
    if plan is None:
        return {}
    return {stop.place_id: stop.name for d in plan.days for stop in d.items}


async def veto_place(
    session: AsyncSession,
    user: AuthenticatedUser,
    trip_id: UUID,
    place_id: UUID,
    on_behalf_of: UUID | None,
) -> VetoResult:
    """File a veto and recompute the plan; the veto is as final as in the app.

    Args:
        session: Open session.
        user: The caller.
        trip_id: Trip id.
        place_id: Catalog place.
        on_behalf_of: Profile id of another person (host or co-host only).

    Returns:
        The veto, the new plan version and what left (and entered) the plan.

    Raises:
        ToolFailedError: No access, not allowed for that person, unknown place,
            a veto already in force, or the rate limit.
    """
    membership = await _member(session, user, trip_id)
    allow_write(user)
    profile_id = await _profile_of(session, membership, on_behalf_of)
    try:
        before = await plan_service.latest_plan(session, membership)
    except PlanNotFoundError:
        before = None
    try:
        veto = await feedback_service.create_veto(
            session, membership, VetoCreate(profile_id=profile_id, place_id=place_id)
        )
    except FeedbackForbiddenError as exc:
        raise ToolFailedError(constants.NOT_ALLOWED) from exc
    except ProfileNotFoundError as exc:
        raise ToolFailedError(constants.PROFILE_NOT_FOUND) from exc
    except FeedbackPlaceNotFoundError as exc:
        raise ToolFailedError(constants.PLACE_NOT_FOUND) from exc
    except VetoExistsError as exc:
        raise ToolFailedError(constants.VETO_EXISTS) from exc
    try:
        after, _ = await plan_service.generate_plan(session, membership, None)
    except PlanInputError:
        return VetoResult(
            veto=veto, plan_version=None, plan_hash=None, removed=[], added=[]
        )
    old, new = _place_names(before), _place_names(after)
    return VetoResult(
        veto=veto,
        plan_version=after.version,
        plan_hash=after.plan_hash,
        removed=sorted(name for pid, name in old.items() if pid not in new),
        added=sorted(name for pid, name in new.items() if pid not in old),
    )
