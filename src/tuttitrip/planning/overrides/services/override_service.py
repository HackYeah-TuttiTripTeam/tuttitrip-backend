"""Host overrides: preview their cost, store them, revoke them, read the log.

A "must" and a block are hard constraints (E0), so the cost of a decision is the
plan with it minus the plan without it. Both are computed in a worker thread. The
reference points ``u*`` of the plan without the decision are reused for the plan
with it (no extra solo runs), so the preview costs one group run more.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from uuid import UUID

import anyio.to_thread
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.logic.hard_constraints import RejectionCode, filter_places
from tuttitrip.planning.logic.params import AlgorithmParams
from tuttitrip.planning.logic.plan_group import GroupPlan, plan_group
from tuttitrip.planning.overrides import db
from tuttitrip.planning.overrides.models import PlanDecision, TripOverride
from tuttitrip.planning.overrides.schemas import (
    DecisionEffects,
    DecisionKind,
    DecisionQuery,
    DecisionRead,
    OverrideCreate,
    OverrideKind,
    OverridePreview,
    OverrideRead,
    PersonDelta,
)
from tuttitrip.planning.parameters.services import parameters_service
from tuttitrip.planning.plans.schemas import ConflictCode, PlanConflict
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.schemas import PlanningInput
from tuttitrip.planning.services.solver_service import configured_solver
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.schemas import TripMembership


class OverrideNotFoundError(Exception):
    """The trip has no such decision."""


class OverrideConflictError(Exception):
    """The decision contradicts a hard constraint; ``conflicts`` say which."""

    def __init__(self, conflicts: list[PlanConflict]) -> None:
        """Keep the conflicts for the response.

        Args:
            conflicts: What the decision runs into.
        """
        super().__init__("The decision contradicts a hard constraint")
        self.conflicts = conflicts


@dataclass(frozen=True, slots=True)
class _Change:
    place_id: UUID
    kind: OverrideKind | None
    """None removes the decision on the place."""


def _apply(planning: PlanningInput, change: _Change) -> PlanningInput:
    must = set(planning.must) - {change.place_id}
    blocked = set(planning.blocked) - {change.place_id}
    if change.kind is OverrideKind.MUST:
        must.add(change.place_id)
    elif change.kind is OverrideKind.BLOCK:
        blocked.add(change.place_id)
    return planning.model_copy(
        update={"must": frozenset(must), "blocked": frozenset(blocked)}
    )


def _effects(base: GroupPlan, changed: GroupPlan) -> DecisionEffects:
    before = {r.person_id: r.r for r in base.people}
    return DecisionEffects(
        d_min_r=changed.min_r - base.min_r,
        d_jain=changed.jain - base.jain,
        d_r=[
            PersonDelta(profile_id=r.person_id, d_r=r.r - before[r.person_id])
            for r in changed.people
        ],
        d_cost=changed.plan.cost.total - base.plan.cost.total,
        d_minutes=_minutes(changed) - _minutes(base),
    )


def _minutes(group: GroupPlan) -> int:
    return sum(d.schedule.active_min for d in group.plan.days)


def _compute(
    planning: PlanningInput,
    changed: PlanningInput,
    alpha: float,
    params: AlgorithmParams,
) -> DecisionEffects:
    solver = configured_solver().solver
    base = plan_group(planning, params, alpha=alpha, solver=solver)
    reference = {r.person_id: r.u_star for r in base.people}
    with_decision = plan_group(
        changed, params, alpha=alpha, u_star=reference, solver=solver
    )
    return _effects(base, with_decision)


def _check(planning: PlanningInput, change: _Change, params: AlgorithmParams) -> None:
    # A "must" that E0 rejects (a veto, hours, stairs, segment) cannot be honoured.
    if change.kind is not OverrideKind.MUST:
        return
    rejected = filter_places(planning, params).reasons(change.place_id)
    if not rejected:
        return
    people = {
        r.person_id
        for r in rejected
        if r.code is RejectionCode.VETO and r.person_id is not None
    }
    raise OverrideConflictError(
        [
            PlanConflict(
                reason_code=ConflictCode.VETO_BLOCKS_PLACE,
                place_id=change.place_id,
                profile_ids=sorted(people, key=str),
                params={"codes": ",".join(sorted({r.code.value for r in rejected}))},
            )
        ]
    )


async def _effects_of(
    session: AsyncSession, membership: TripMembership, change: _Change
) -> DecisionEffects:
    planning, _, alpha = await plan_service.gather_input(session, membership)
    changed = _apply(planning, change)
    _, params = await parameters_service.current(session)
    _check(changed, change, params)
    await session.rollback()  # no transaction while computing
    return await anyio.to_thread.run_sync(
        partial(_compute, planning, changed, alpha, params)
    )


async def preview(
    session: AsyncSession, membership: TripMembership, data: OverrideCreate
) -> OverridePreview:
    """The cost of a decision; nothing is stored.

    Args:
        session: Open session.
        membership: The host's membership.
        data: The decision.

    Returns:
        Change of ``min r``, Jain's index, ``r`` per person, cost and time.

    Raises:
        OverrideConflictError: When a "must" runs into a veto or another E0 rule.
    """
    effects = await _effects_of(session, membership, _Change(data.place_id, data.kind))
    return OverridePreview(kind=data.kind, place_id=data.place_id, effects=effects)


def _read(row: TripOverride, effects: DecisionEffects | None) -> OverrideRead:
    return OverrideRead.model_validate(
        {
            "id": row.id,
            "trip_id": row.trip_id,
            "place_id": row.place_id,
            "kind": row.kind,
            "reason": row.reason,
            "created_by_sub": row.created_by_sub,
            "created_at": row.created_at,
            "revoked_at": row.revoked_at,
            "effects": effects,
        }
    )


def _log(
    membership: TripMembership,
    kind: DecisionKind,
    place_id: UUID,
    reason: str | None,
    effects: DecisionEffects,
) -> PlanDecision:
    return PlanDecision(
        trip_id=membership.trip_id,
        kind=kind.value,
        place_id=place_id,
        reason=reason,
        effects=effects.model_dump(mode="json"),
        created_by_sub=membership.sub,
    )


async def create_override(
    session: AsyncSession, membership: TripMembership, data: OverrideCreate
) -> OverrideRead:
    """Store a decision and its entry in the log.

    A new decision on a place replaces the one in force.

    Args:
        session: Open session.
        membership: The host's membership.
        data: The decision.

    Returns:
        The decision with the effects that were logged.

    Raises:
        OverrideConflictError: When a "must" runs into a veto or another E0 rule.
    """
    effects = await _effects_of(session, membership, _Change(data.place_id, data.kind))
    for old in await db.select_active(session, membership.trip_id):
        if old.place_id == data.place_id:
            old.revoked_at = datetime.now(UTC)
            old.revoked_by_sub = membership.sub
    await session.flush()
    row = TripOverride(
        trip_id=membership.trip_id,
        place_id=data.place_id,
        kind=data.kind.value,
        reason=data.reason,
        created_by_sub=membership.sub,
    )
    session.add(row)
    session.add(
        _log(
            membership,
            DecisionKind(data.kind.value),
            data.place_id,
            data.reason,
            effects,
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:  # two hosts at once, or an unknown place
        await session.rollback()
        raise OverrideConflictError([]) from exc
    await session.refresh(row)
    return _read(row, effects)


async def revoke_override(
    session: AsyncSession, membership: TripMembership, override_id: UUID
) -> OverrideRead:
    """Take a decision back; the log records what that changed.

    Args:
        session: Open session.
        membership: The host's membership.
        override_id: The decision.

    Returns:
        The revoked decision.

    Raises:
        OverrideNotFoundError: When the trip has no such decision.
    """
    row = await db.select_override(session, membership.trip_id, override_id)
    if row is None:
        raise OverrideNotFoundError(str(override_id))
    if row.revoked_at is not None:
        return _read(row, None)
    effects = await _effects_of(session, membership, _Change(row.place_id, None))
    row = await db.select_override(session, membership.trip_id, override_id)
    if row is None:  # pragma: no cover - deleted with the trip meanwhile
        raise OverrideNotFoundError(str(override_id))
    row.revoked_at = datetime.now(UTC)
    row.revoked_by_sub = membership.sub
    session.add(
        _log(membership, DecisionKind.REVOKE, row.place_id, row.reason, effects)
    )
    await session.commit()
    await session.refresh(row)
    return _read(row, effects)


async def list_decisions(
    session: AsyncSession, membership: TripMembership, query: DecisionQuery
) -> Page[DecisionRead]:
    """One page of the decision log.

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.
        query: Page, sort and filters.

    Returns:
        The entries, newest first by default.
    """
    page = await db.select_decisions(session, membership.trip_id, query)
    items = [
        DecisionRead.model_validate(
            {
                "id": d.id,
                "trip_id": d.trip_id,
                "kind": d.kind,
                "place_id": d.place_id,
                "reason": d.reason,
                "effects": DecisionEffects.model_validate(d.effects),
                "created_by_sub": d.created_by_sub,
                "created_at": d.created_at,
            }
        )
        for d in page.items
    ]
    return Page(
        items=items, total=page.total, page=page.page, size=page.size, pages=page.pages
    )
