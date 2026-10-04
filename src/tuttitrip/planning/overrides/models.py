"""Host overrides and the append-only decision log."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class TripOverride(Base):
    """A host decision: force a place into the plan ("must") or block it.

    Both are hard constraints of E0. A decision stays in the table after it is
    revoked, so the log can still name it.
    """

    __tablename__ = "trip_overrides"
    __table_args__ = (
        CheckConstraint("kind IN ('must', 'block')", name="kind"),
        # At most one decision in force per place.
        Index(
            "uq_trip_overrides_active",
            "trip_id",
            "place_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("places.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str | None] = mapped_column(Text)
    created_by_sub: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None]
    revoked_by_sub: Mapped[str | None] = mapped_column(String(255))


class PlanDecision(Base):
    """Append-only log of host decisions and budget approvals.

    A trigger refuses UPDATE and TRUNCATE, and DELETE except the cascade of
    deleting the trip. ``effects`` holds the cost of the decision as computed
    when it was made: ``d_min_r``, ``d_jain``, ``d_r`` per person, ``d_cost`` and
    ``d_minutes``.
    """

    __tablename__ = "plan_decisions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('must', 'block', 'revoke', 'budget_approval')", name="kind"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    place_id: Mapped[uuid.UUID | None]
    reason: Mapped[str | None] = mapped_column(Text)
    effects: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by_sub: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
