"""Plan proposals and the answers of the members."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class PlanProposal(Base):
    """A plan version the host sent to the members; removed with the trip.

    It is pinned to one stored version (``plan_id``, ``plan_hash``): a new plan
    version makes it ``superseded`` instead of changing what somebody approved.
    """

    __tablename__ = "plan_proposals"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'superseded')", name="status"),
        # One proposal is open at a time; sending a new one supersedes the old.
        Index(
            "uq_plan_proposals_open",
            "trip_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_plan_proposals_trip_sent_at", "trip_id", "sent_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE")
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="CASCADE"), index=True
    )
    plan_hash: Mapped[str] = mapped_column(String(12))
    sent_by: Mapped[str] = mapped_column(String(255))
    sent_at: Mapped[datetime] = mapped_column(server_default=func.now())
    status: Mapped[str] = mapped_column(String(16), default="open")


class ProposalResponse(Base):
    """One member's answer; a new answer replaces the earlier one."""

    __tablename__ = "proposal_responses"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approve', 'reject', 'comment')", name="decision"
        ),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan_proposals.id", ondelete="CASCADE"), primary_key=True
    )
    member_sub: Mapped[str] = mapped_column(String(255), primary_key=True)
    decision: Mapped[str] = mapped_column(String(8))
    remark: Mapped[str | None] = mapped_column(Text)
    responded_at: Mapped[datetime] = mapped_column(server_default=func.now())
