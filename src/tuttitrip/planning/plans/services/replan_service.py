"""Replan the rest of a day on a stored plan (backend#74; an extension).

Nothing is stored: the answer is the replanned day and what changed. The work is
the same evaluator as the plan itself, on one day, in milliseconds, in a worker
thread. The clock is the ``as_of`` of the request.
"""

import time
from datetime import datetime
from functools import partial
from uuid import UUID
from zoneinfo import ZoneInfo

import anyio.to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.logic.params import DEFAULT_PARAMS
from tuttitrip.planning.logic.replan import DayState, ReplanResult, replan_rest_of_day
from tuttitrip.planning.logic.solver import sorted_ids
from tuttitrip.planning.plans.logic.input_builder import PlanInputError
from tuttitrip.planning.plans.logic.read_model import day_stops
from tuttitrip.planning.plans.schemas import (
    PlanRead,
    ReplanChange,
    ReplanChangeKind,
    ReplanRead,
    ReplanRequest,
    ReplanStatus,
)
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.schemas import PlanningInput
from tuttitrip.profiles.services import profile_service
from tuttitrip.trips.schemas import TripMembership, TripRole

AFFECTED_POINTS = 0.5
"""A change touches a person when their welfare moves by this much."""


def _starts(plan: PlanRead, day: int, zone: ZoneInfo) -> DayState:
    items = plan.days[day].items
    when = plan.days[day].date
    assert when is not None  # ruff: ignore[assert] stored plans always carry the date
    return DayState(
        place_ids=tuple(i.place_id for i in items),
        starts={
            i.place_id: datetime.combine(when, i.start, tzinfo=zone) for i in items
        },
    )


def _nights(plan: PlanRead) -> tuple[UUID, ...]:
    lodging = plan.lodging
    if lodging is None or lodging.place_id is None:
        return ()
    odd = {e.night: e.place_id for e in lodging.exceptional}
    return tuple(odd.get(n, lodging.place_id) for n in range(1, lodging.nights + 1))


def _compute(
    planning: PlanningInput,
    plan: PlanRead,
    day: int,
    as_of: datetime,
    zone: ZoneInfo,
) -> ReplanResult:
    current = tuple(sorted_ids([i.place_id for i in d.items]) for d in plan.days)
    floors = {p.profile_id: p.floor_eff for p in plan.fairness.per_person}
    return replan_rest_of_day(
        planning,
        current,
        day=day,
        state=_starts(plan, day, zone),
        as_of=as_of.astimezone(zone),
        lodging_nights=_nights(plan),
        floors=floors,
        alpha=plan.params.alpha,
        params=DEFAULT_PARAMS,
    )


async def status_of(
    session: AsyncSession,
    membership: TripMembership,
    affected: list[UUID],
) -> ReplanStatus:
    """Whether a replan is in force or waits for the host.

    Args:
        session: Open session.
        membership: The caller's membership.
        affected: The people the change touches.

    Returns:
        ``active`` for a host or co-host, or when the change touches nobody but
        the caller; ``pending_host`` when a member's change touches others.
    """
    if membership.role.satisfies(TripRole.CO_HOST) or not affected:
        return ReplanStatus.ACTIVE
    own = await profile_service.find_account_profile(
        session, membership.trip_id, membership.sub
    )
    others = [pid for pid in affected if pid != own]
    return ReplanStatus.PENDING_HOST if others else ReplanStatus.ACTIVE


async def replan(
    session: AsyncSession,
    membership: TripMembership,
    plan_id: UUID,
    request: ReplanRequest,
) -> ReplanRead:
    """Replan the rest of a day of a stored plan.

    Args:
        session: Open session.
        membership: The caller's membership (any role).
        plan_id: The stored version to start from.
        request: Context, day and the moment of the replan.

    Returns:
        The day after the replan, the changes, whom they touch and whether they
        are in force (a host's) or wait for the host (a member's, touching others).

    Raises:
        PlanNotFoundError: When the trip has no such version.
        PlanInputError: When the day is not in the plan or the trip cannot be planned.
    """
    started = time.perf_counter()
    plan = await plan_service.get_plan(session, membership, plan_id)
    if request.day > len(plan.days):
        msg = f"The plan has {len(plan.days)} days"
        raise PlanInputError(msg)
    planning, _, _ = await plan_service.gather_input(session, membership)
    await session.rollback()  # no transaction while computing
    zone = ZoneInfo(planning.trip.timezone)
    day = request.day - 1
    result = await anyio.to_thread.run_sync(
        partial(_compute, planning, plan, day, request.as_of, zone)
    )
    places = {p.id: p for p in planning.places}
    schedule = result.evaluation.schedules[day]
    before = (
        {s.person_id: s.welfare for s in result.before.scores} if result.before else {}
    )
    after = {s.person_id: s.welfare for s in result.evaluation.scores}
    affected = sorted_ids(
        pid
        for pid, u in after.items()
        if abs(u - before.get(pid, u + AFFECTED_POINTS)) >= AFFECTED_POINTS
    )
    changes = (
        [
            ReplanChange(kind=ReplanChangeKind.REMOVED, place_id=p, name=places[p].name)
            for p in result.removed
            if p in places
        ]
        + [
            ReplanChange(kind=ReplanChangeKind.ADDED, place_id=p, name=places[p].name)
            for p in result.added
        ]
        + [
            ReplanChange(
                kind=ReplanChangeKind.MOVED,
                place_id=p,
                name=places[p].name,
                shift_min=minutes,
            )
            for p, minutes in result.shifted
        ]
    )
    status = (
        await status_of(session, membership, list(affected))
        if changes
        else ReplanStatus.ACTIVE
    )
    return ReplanRead(
        context=request.context,
        day=request.day,
        as_of=request.as_of,
        status=status,
        stops=day_stops(planning, schedule.visits, places),
        changes=changes,
        affected=list(affected),
        j_replan=result.j_replan,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
