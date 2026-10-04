"""Host consent to exceeding the budget (E6)."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class BudgetApproval(Base):
    """The question "may the plan cost more than ``B_do``"; removed with the trip.

    Created together with a plan version that needs approval (``flex_plan_id``,
    ``P_flex``) and its alternative within the limit (``strict_plan_id``,
    ``P_strict``). The amount, the price per point and the person who gains most
    are copies made when the plan was computed. A decision is written once: the
    row never leaves ``approved`` or ``rejected``, and the same entry goes to the
    append-only ``plan_decisions`` log.
    """

    __tablename__ = "budget_approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'superseded')", name="status"
        ),
        CheckConstraint(
            "(status IN ('approved', 'rejected')) = (decided_by_sub IS NOT NULL)",
            name="decided",
        ),
        # One question is open at a time; a new plan version supersedes it.
        Index(
            "uq_budget_approvals_pending",
            "trip_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_budget_approvals_trip_created_at", "trip_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE")
    )
    flex_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="CASCADE"), index=True
    )
    strict_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="CASCADE")
    )
    currency: Mapped[str] = mapped_column(String(3))
    over_budget: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    kappa: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    gain_profile_id: Mapped[uuid.UUID | None]
    gain_points: Mapped[float | None]
    status: Mapped[str] = mapped_column(String(16), default="pending")
    active_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    decided_by_sub: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime | None]
