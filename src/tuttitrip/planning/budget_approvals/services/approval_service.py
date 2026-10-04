"""Host consent to exceeding the budget (E6): ask, list, approve, reject.

A plan version that needs approval (``P_flex``) opens one question, in the
transaction that stores the version; the host gets a notification. Approving keeps
``P_flex`` as the plan in force. Rejecting stores ``P_strict`` as the newest
version, so it becomes the plan in force (the input hash stays the same, so the
host's choice holds until the input changes). Every decision is written to the
append-only ``plan_decisions`` log with the amount, the price per point and who
gains most; a decided question never changes again. Any new plan version
supersedes a pending question.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.notifications.schemas import (
    NotificationAction,
    NotificationActionCode,
    NotificationType,
)
from tuttitrip.notifications.services import notification_service
from tuttitrip.planning.budget_approvals import db
from tuttitrip.planning.budget_approvals.logic.effects import consent_effects
from tuttitrip.planning.budget_approvals.models import BudgetApproval
from tuttitrip.planning.budget_approvals.schemas import (
    BudgetApprovalQuery,
    BudgetApprovalRead,
    BudgetApprovalStatus,
    BudgetDecisionCreate,
)
from tuttitrip.planning.overrides import db as overrides_db
from tuttitrip.planning.overrides.models import PlanDecision
from tuttitrip.planning.overrides.schemas import (
    BudgetConsent,
    BudgetOutcome,
    DecisionKind,
)
from tuttitrip.planning.plans import db as plans_db
from tuttitrip.planning.plans.logic.stored import stored_plan
from tuttitrip.planning.plans.models import PlanVersion
from tuttitrip.planning.plans.schemas import PlanRead
from tuttitrip.planning.proposals.services import proposal_service
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import member_service


class ApprovalNotFoundError(Exception):
    """The trip has no such consent question."""


class ApprovalNotPendingError(Exception):
    """The question was decided already, or the plan was recomputed."""

    def __init__(self, status: BudgetApprovalStatus) -> None:
        """Keep the state the question is in.

        Args:
            status: Its current status.
        """
        super().__init__(f"The budget approval is {status.value}")
        self.status = status


def _key(approval_id: UUID) -> str:
    return f"budget:{approval_id}"


def _plan(row: PlanVersion) -> PlanRead:
    return stored_plan(
        plan_id=row.id,
        trip_id=row.trip_id,
        version=row.version,
        input_hash=row.input_hash,
        plan_hash=row.plan_hash,
        created_at=row.created_at,
        params=row.params,
        result=row.result,
    )


async def open_for_plan(
    session: AsyncSession, flex: PlanVersion, strict: PlanVersion
) -> BudgetApproval | None:
    """Ask the host about a stored ``P_flex`` (in the transaction that stored it).

    Args:
        session: Open session (the caller commits).
        flex: The new main version.
        strict: Its alternative within ``B_do``.

    Returns:
        The question, or None when the version does not need approval.
    """
    budget = flex.result["budget"]
    if not budget["needs_approval"]:
        return None
    row = BudgetApproval(
        trip_id=flex.trip_id,
        flex_plan_id=flex.id,
        strict_plan_id=strict.id,
        currency=budget["currency"],
        over_budget=Decimal(budget["over_budget"]),
        kappa=Decimal(budget["kappa"]),
        gain_profile_id=budget["gain_profile_id"] and UUID(budget["gain_profile_id"]),
        gain_points=budget["gain_points"],
        status=BudgetApprovalStatus.PENDING.value,
    )
    session.add(row)
    await session.flush()
    roles = await member_service.account_roles(session, flex.trip_id)
    await notification_service.notify(
        session,
        recipients=[sub for sub, role in roles.items() if role is TripRole.HOST],
        type=NotificationType.BUDGET_APPROVAL_WAITING,
        trip_id=flex.trip_id,
        params={
            "approval_id": str(row.id),
            "over_budget": str(row.over_budget),
            "kappa": str(row.kappa),
        },
        actions=[
            NotificationAction(
                code=NotificationActionCode.APPROVE_BUDGET,
                params={"approval_id": str(row.id)},
            ),
            NotificationAction(
                code=NotificationActionCode.REJECT_BUDGET,
                params={"approval_id": str(row.id)},
            ),
            NotificationAction(
                code=NotificationActionCode.OPEN_PLAN, params={"plan_id": str(flex.id)}
            ),
        ],
        dedupe_key=_key(row.id),
    )
    return row


async def on_plan_changed(session: AsyncSession, trip_id: UUID) -> None:
    """Supersede the pending question; call it when a new version is stored.

    Does not commit and does not lock: the caller already holds the plans lock.

    Args:
        session: Open session.
        trip_id: Trip id.
    """
    for old in await db.supersede_pending(session, trip_id):
        await notification_service.resolve(session, _key(old))


async def status_of_plan(
    session: AsyncSession, plan_id: UUID
) -> BudgetApprovalStatus | None:
    """The state of the question asked for a ``P_flex`` version.

    Args:
        session: Open session.
        plan_id: The version.

    Returns:
        The status, or None when no question was asked for it.
    """
    row = await db.select_for_plan(session, plan_id)
    return None if row is None else BudgetApprovalStatus(row.status)


async def _names(session: AsyncSession, membership: TripMembership) -> dict[UUID, str]:
    host_view = membership.model_copy(update={"role": TripRole.HOST})
    profiles = await profile_service.list_profiles(session, host_view)
    return {p.id: p.display_name for p in profiles}


def _read(row: BudgetApproval, names: dict[UUID, str]) -> BudgetApprovalRead:
    return BudgetApprovalRead(
        id=row.id,
        trip_id=row.trip_id,
        flex_plan_id=row.flex_plan_id,
        strict_plan_id=row.strict_plan_id,
        currency=row.currency,
        over_budget=row.over_budget,
        kappa=row.kappa,
        gain_profile_id=row.gain_profile_id,
        gain_profile_name=names.get(row.gain_profile_id)
        if row.gain_profile_id
        else None,
        gain_points=row.gain_points,
        status=BudgetApprovalStatus(row.status),
        active_plan_id=row.active_plan_id,
        created_at=row.created_at,
        decided_by_sub=row.decided_by_sub,
        decided_at=row.decided_at,
    )


async def list_approvals(
    session: AsyncSession, membership: TripMembership, query: BudgetApprovalQuery
) -> Page[BudgetApprovalRead]:
    """One page of the trip's consent questions.

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.
        query: Page, sort and filters.

    Returns:
        The questions, newest first by default.
    """
    page = await db.select_page(session, membership.trip_id, query)
    names = await _names(session, membership)
    return Page[BudgetApprovalRead](
        items=[_read(row, names) for row in page.items],
        total=page.total,
        page=page.page,
        size=page.size,
        pages=page.pages,
    )


async def _load_pending(
    session: AsyncSession, membership: TripMembership, approval_id: UUID
) -> BudgetApproval:
    await plans_db.lock_trip_plans(session, membership.trip_id)
    row = await db.select_one(session, membership.trip_id, approval_id)
    if row is None:
        raise ApprovalNotFoundError(str(approval_id))
    if row.status != BudgetApprovalStatus.PENDING.value:
        raise ApprovalNotPendingError(BudgetApprovalStatus(row.status))
    return row


async def _decide(
    session: AsyncSession,
    membership: TripMembership,
    approval_id: UUID,
    data: BudgetDecisionCreate,
    outcome: BudgetOutcome,
) -> BudgetApprovalRead:
    row = await _load_pending(session, membership, approval_id)
    flex = await plans_db.select_by_id(session, row.trip_id, row.flex_plan_id)
    strict = await plans_db.select_by_id(session, row.trip_id, row.strict_plan_id)
    if flex is None or strict is None:  # pragma: no cover - cascades with the trip
        raise ApprovalNotFoundError(str(approval_id))
    consent = BudgetConsent(
        approval_id=row.id,
        outcome=outcome,
        currency=row.currency,
        over_budget=row.over_budget,
        kappa=row.kappa,
        gain_profile_id=row.gain_profile_id,
        gain_points=row.gain_points,
    )
    overrides_db.log_decision(
        session,
        PlanDecision(
            trip_id=row.trip_id,
            kind=DecisionKind.BUDGET_APPROVAL.value,
            reason=data.reason,
            effects=consent_effects(_plan(flex), _plan(strict), consent).model_dump(
                mode="json"
            ),
            created_by_sub=membership.sub,
        ),
    )
    row.status = BudgetApprovalStatus(outcome.value).value
    row.decided_by_sub = membership.sub
    row.decided_at = datetime.now(UTC)
    if outcome is BudgetOutcome.APPROVED:
        row.active_plan_id = flex.id
    else:
        latest = await plans_db.select_latest(session, row.trip_id)
        version = PlanVersion(
            id=uuid.uuid4(),
            trip_id=row.trip_id,
            version=(latest.version if latest else flex.version) + 1,
            input_hash=flex.input_hash,
            plan_hash=strict.plan_hash,
            params=strict.params,
            result=strict.result,
            created_by_sub=membership.sub,
        )
        session.add(version)
        await session.flush()
        row.active_plan_id = version.id
        await proposal_service.on_plan_changed(session, row.trip_id)
    await notification_service.resolve(session, _key(row.id))
    await session.commit()
    await session.refresh(row)
    return _read(row, await _names(session, membership))


async def approve(
    session: AsyncSession,
    membership: TripMembership,
    approval_id: UUID,
    data: BudgetDecisionCreate,
) -> BudgetApprovalRead:
    """The host accepts ``P_flex``; the decision is logged.

    Args:
        session: Open session.
        membership: The host's membership.
        approval_id: The question.
        data: Optional reason.

    Returns:
        The decided question.

    Raises:
        ApprovalNotFoundError: When the trip has no such question.
        ApprovalNotPendingError: When it was decided or superseded.
    """
    return await _decide(session, membership, approval_id, data, BudgetOutcome.APPROVED)


async def reject(
    session: AsyncSession,
    membership: TripMembership,
    approval_id: UUID,
    data: BudgetDecisionCreate,
) -> BudgetApprovalRead:
    """The host refuses; ``P_strict`` becomes the plan in force; the decision is logged.

    Args:
        session: Open session.
        membership: The host's membership.
        approval_id: The question.
        data: Optional reason.

    Returns:
        The decided question.

    Raises:
        ApprovalNotFoundError: When the trip has no such question.
        ApprovalNotPendingError: When it was decided or superseded.
    """
    return await _decide(session, membership, approval_id, data, BudgetOutcome.REJECTED)
