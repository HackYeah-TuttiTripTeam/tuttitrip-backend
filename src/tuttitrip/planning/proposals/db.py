"""Proposal queries on PostgreSQL."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.proposals.models import PlanProposal, ProposalResponse
from tuttitrip.planning.proposals.schemas import ProposalDecision

OPEN = "open"
SUPERSEDED = "superseded"


async def select_proposal(
    session: AsyncSession, trip_id: UUID, proposal_id: UUID
) -> PlanProposal | None:
    """One proposal of a trip.

    Args:
        session: Open session.
        trip_id: Trip id (a proposal of another trip is not found).
        proposal_id: Proposal id.

    Returns:
        The row, or None.
    """
    return await session.scalar(
        select(PlanProposal).where(
            PlanProposal.trip_id == trip_id, PlanProposal.id == proposal_id
        )
    )


async def select_newest(session: AsyncSession, trip_id: UUID) -> PlanProposal | None:
    """The proposal sent last, open or not.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The row, or None when nothing was sent.
    """
    return await session.scalar(
        select(PlanProposal)
        .where(PlanProposal.trip_id == trip_id)
        .order_by(PlanProposal.sent_at.desc(), PlanProposal.id.desc())
        .limit(1)
    )


async def select_for_plans(
    session: AsyncSession, trip_id: UUID, plan_id: UUID
) -> Sequence[PlanProposal]:
    """Proposals of one plan version.

    Args:
        session: Open session.
        trip_id: Trip id.
        plan_id: Plan version id.

    Returns:
        The rows, newest first.
    """
    result = await session.scalars(
        select(PlanProposal)
        .where(PlanProposal.trip_id == trip_id, PlanProposal.plan_id == plan_id)
        .order_by(PlanProposal.sent_at.desc(), PlanProposal.id.desc())
    )
    return result.all()


async def supersede_open(session: AsyncSession, trip_id: UUID) -> Sequence[UUID]:
    """Mark the open proposal of a trip superseded.

    Args:
        session: Open session (the caller commits).
        trip_id: Trip id.

    Returns:
        Ids of the proposals that changed (at most one).
    """
    result = await session.execute(
        update(PlanProposal)
        .where(PlanProposal.trip_id == trip_id, PlanProposal.status == OPEN)
        .values(status=SUPERSEDED)
        .returning(PlanProposal.id)
    )
    return [row[0] for row in result.all()]


async def select_responses(
    session: AsyncSession, proposal_id: UUID
) -> Sequence[ProposalResponse]:
    """The answers to a proposal.

    Args:
        session: Open session.
        proposal_id: Proposal id.

    Returns:
        The rows, oldest first.
    """
    result = await session.scalars(
        select(ProposalResponse)
        .where(ProposalResponse.proposal_id == proposal_id)
        .order_by(ProposalResponse.responded_at, ProposalResponse.member_sub)
    )
    return result.all()


async def upsert_response(
    session: AsyncSession,
    proposal_id: UUID,
    sub: str,
    decision: ProposalDecision,
    remark: str | None,
) -> None:
    """Store a member's answer, replacing an earlier one.

    Args:
        session: Open session (the caller commits).
        proposal_id: Proposal id.
        sub: The member.
        decision: What they decided.
        remark: Their remark, if any.
    """
    stmt = insert(ProposalResponse).values(
        proposal_id=proposal_id, member_sub=sub, decision=decision.value, remark=remark
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["proposal_id", "member_sub"],
            set_={
                "decision": stmt.excluded.decision,
                "remark": stmt.excluded.remark,
                "responded_at": func.now(),
            },
        )
    )
