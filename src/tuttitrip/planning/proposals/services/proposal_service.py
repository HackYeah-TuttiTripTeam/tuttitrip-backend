"""Plan proposals: the host sends a version, the members approve or comment.

Every member with an account approves, rejects or comments.

A proposal is pinned to one version. When the plan changes, ``on_plan_changed``
(called by the services that store a new version, in their transaction) makes the
open proposal ``superseded`` and clears its notifications; answering it then is a
409. Plan writes of a trip are serialised by the plans advisory lock, so a
member's answer never lands on a version that was replaced a moment before.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.notifications.schemas import (
    NotificationAction,
    NotificationActionCode,
    NotificationType,
)
from tuttitrip.notifications.services import notification_service
from tuttitrip.planning.plans import db as plans_db
from tuttitrip.planning.plans.models import PlanVersion
from tuttitrip.planning.proposals import db
from tuttitrip.planning.proposals.logic.status import status_of, tally
from tuttitrip.planning.proposals.models import PlanProposal
from tuttitrip.planning.proposals.schemas import (
    ProfileWithoutAccount,
    ProposalCreate,
    ProposalDecision,
    ProposalRead,
    ResponseCreate,
    ResponseRead,
)
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import member_service

UNKNOWN_NAME = "?"


class ProposalNotFoundError(Exception):
    """The trip has no such proposal (or no plan to propose)."""


class ProposalOutdatedError(Exception):
    """The proposal, or the plan to send, is not the latest version."""

    def __init__(self, latest_plan_id: UUID) -> None:
        """Keep the version the plan is at now.

        Args:
            latest_plan_id: The latest stored version of the trip.
        """
        super().__init__("The proposal is about an older plan version")
        self.latest_plan_id = latest_plan_id


def _key(proposal_id: UUID) -> str:
    return f"proposal:{proposal_id}"


async def _profiles(
    session: AsyncSession, membership: TripMembership
) -> list[ProfileRead]:
    host_view = membership.model_copy(update={"role": TripRole.HOST})
    return await profile_service.list_profiles(session, host_view)


async def _read(
    session: AsyncSession, membership: TripMembership, row: PlanProposal
) -> ProposalRead:
    plan = await plans_db.select_by_id(session, row.trip_id, row.plan_id)
    latest = await plans_db.select_latest(session, row.trip_id)
    if plan is None or latest is None:  # pragma: no cover - cascades with the trip
        raise ProposalNotFoundError(str(row.id)) from None
    roles = await member_service.account_roles(session, row.trip_id)
    profiles = await _profiles(session, membership)
    by_sub = {p.user_sub: p for p in profiles if p.user_sub is not None}
    answers = await db.select_responses(session, row.id)
    counts = tally(roles, {a.member_sub: ProposalDecision(a.decision) for a in answers})
    outdated = row.status != db.OPEN or latest.id != row.plan_id
    return ProposalRead(
        id=row.id,
        trip_id=row.trip_id,
        plan_id=row.plan_id,
        plan_hash=row.plan_hash,
        plan_version=plan.version,
        sent_by_name=by_sub[row.sent_by].display_name
        if row.sent_by in by_sub
        else UNKNOWN_NAME,
        sent_at=row.sent_at,
        status=status_of(counts, outdated=outdated),
        tally=counts,
        responses=[
            ResponseRead(
                profile_id=by_sub[a.member_sub].id if a.member_sub in by_sub else None,
                display_name=by_sub[a.member_sub].display_name
                if a.member_sub in by_sub
                else UNKNOWN_NAME,
                decision=ProposalDecision(a.decision),
                remark=a.remark,
                responded_at=a.responded_at,
                is_me=a.member_sub == membership.sub,
            )
            for a in answers
            if a.member_sub in roles
        ],
        profiles_without_account=[
            ProfileWithoutAccount(profile_id=p.id, display_name=p.display_name)
            for p in profiles
            if p.user_sub is None
        ],
    )


async def _plan_to_send(
    session: AsyncSession, trip_id: UUID, plan_id: UUID | None
) -> PlanVersion:
    latest = await plans_db.select_latest(session, trip_id)
    if latest is None:
        raise ProposalNotFoundError(str(trip_id))
    if plan_id is None or plan_id == latest.id:
        return latest
    if await plans_db.select_by_id(session, trip_id, plan_id) is None:
        raise ProposalNotFoundError(str(plan_id))
    raise ProposalOutdatedError(latest.id)


async def send_proposal(
    session: AsyncSession, membership: TripMembership, data: ProposalCreate
) -> ProposalRead:
    """Send the latest plan version (or the given one, if it still is) to the members.

    Sending again replaces the open proposal. The host sends it, which counts as
    their approval. Everybody else with an account gets a notification in the
    same transaction.

    Args:
        session: Open session.
        membership: The host's membership.
        data: Which version, the latest by default.

    Returns:
        The new proposal.

    Raises:
        ProposalNotFoundError: When the trip has no plan, or no such version.
        ProposalOutdatedError: When the version is not the latest one.
    """
    await plans_db.lock_trip_plans(session, membership.trip_id)
    plan = await _plan_to_send(session, membership.trip_id, data.plan_id)
    for old in await db.supersede_open(session, membership.trip_id):
        await notification_service.resolve(session, _key(old))
    row = PlanProposal(
        trip_id=membership.trip_id,
        plan_id=plan.id,
        plan_hash=plan.plan_hash,
        sent_by=membership.sub,
        status=db.OPEN,
    )
    session.add(row)
    await session.flush()
    await db.upsert_response(
        session, row.id, membership.sub, ProposalDecision.APPROVE, None
    )
    roles = await member_service.account_roles(session, membership.trip_id)
    await notification_service.notify(
        session,
        recipients=roles,
        type=NotificationType.PROPOSAL_WAITING,
        trip_id=membership.trip_id,
        params={"proposal_id": str(row.id), "plan_id": str(plan.id)},
        actions=[
            NotificationAction(
                code=NotificationActionCode.APPROVE_PROPOSAL,
                params={"proposal_id": str(row.id)},
            ),
            NotificationAction(
                code=NotificationActionCode.OPEN_PLAN,
                params={"plan_id": str(plan.id)},
            ),
        ],
        dedupe_key=_key(row.id),
        actor=membership.sub,
    )
    await session.commit()
    await session.refresh(row)
    return await _read(session, membership, row)


async def current_proposal(
    session: AsyncSession, membership: TripMembership
) -> ProposalRead:
    """The proposal sent last, with its status (``outdated`` when the plan moved on).

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.

    Returns:
        The proposal.

    Raises:
        ProposalNotFoundError: When nothing was sent.
    """
    row = await db.select_newest(session, membership.trip_id)
    if row is None:
        raise ProposalNotFoundError(str(membership.trip_id))
    return await _read(session, membership, row)


async def respond(
    session: AsyncSession,
    membership: TripMembership,
    proposal_id: UUID,
    data: ResponseCreate,
) -> ProposalRead:
    """Store the caller's answer; it replaces their earlier one.

    The caller's own notification is cleared (the others still wait for their
    turn; nobody else is notified of an answer, the counts show it).

    Args:
        session: Open session.
        membership: The caller's membership (any role).
        proposal_id: The proposal.
        data: The decision and the remark.

    Returns:
        The proposal with the new counts.

    Raises:
        ProposalNotFoundError: When the trip has no such proposal.
        ProposalOutdatedError: When the plan changed after it was sent.
    """
    await plans_db.lock_trip_plans(session, membership.trip_id)
    row = await db.select_proposal(session, membership.trip_id, proposal_id)
    if row is None:
        raise ProposalNotFoundError(str(proposal_id))
    latest = await plans_db.select_latest(session, membership.trip_id)
    if latest is None or row.status != db.OPEN or latest.id != row.plan_id:
        raise ProposalOutdatedError(latest.id if latest else row.plan_id)
    await db.upsert_response(
        session, row.id, membership.sub, data.decision, data.remark
    )
    await notification_service.resolve(session, _key(row.id), user_sub=membership.sub)
    await session.commit()
    return await _read(session, membership, row)


async def on_plan_changed(session: AsyncSession, trip_id: UUID) -> None:
    """Make the open proposal ``superseded``; call it when a new version is stored.

    Does not commit and does not lock: the caller already holds the plans lock.

    Args:
        session: Open session.
        trip_id: Trip id.
    """
    for old in await db.supersede_open(session, trip_id):
        await notification_service.resolve(session, _key(old))


async def approved_at(
    session: AsyncSession, membership: TripMembership, plan_id: UUID
) -> datetime | None:
    """When every member with an account had approved this plan version.

    A version stays approved after the plan moves on (the file of that version is
    what the members signed), but a member who joined later has not approved it.

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.
        plan_id: The plan version.

    Returns:
        The time of the last approval, or None when the version is not approved.
    """
    roles = await member_service.account_roles(session, membership.trip_id)
    for row in await db.select_for_plans(session, membership.trip_id, plan_id):
        answers = await db.select_responses(session, row.id)
        approvals = {
            a.member_sub: a.responded_at
            for a in answers
            if a.decision == ProposalDecision.APPROVE.value
        }
        if roles and all(sub in approvals for sub in roles):
            return max(approvals[sub] for sub in roles)
    return None
