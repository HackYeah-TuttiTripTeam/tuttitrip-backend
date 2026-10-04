"""State of the "anyway" suggestions: the host's rejections and the model's text."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class AnywayState(Base):
    """A suggestion (day and place of a plan version) with what happened to it.

    ``rejected_at`` set means the host said no: the place does not come back on
    that day in any later version of the trip's plan. ``justification`` is the
    text the model wrote for the version (``write_justifications``).
    """

    __tablename__ = "anyway_states"
    __table_args__ = (
        UniqueConstraint("plan_id", "day", "place_id"),
        Index(
            "ix_anyway_states_rejected",
            "trip_id",
            postgresql_where=text("rejected_at IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE")
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="CASCADE")
    )
    day: Mapped[int]
    place_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("places.id", ondelete="CASCADE")
    )
    rejected_at: Mapped[datetime | None]
    rejected_by_sub: Mapped[str | None] = mapped_column(String(255))
    justification: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
