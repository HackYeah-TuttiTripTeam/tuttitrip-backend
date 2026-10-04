"""Stored plan versions."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class PlanVersion(Base):
    """One computed plan of a trip; removed together with the trip.

    ``result`` is a copy made when the plan was computed (prices with their
    source and markup included), so a later change of the catalog does not
    rewrite history. ``params`` holds the knobs and the algorithm parameters
    the plan was computed with.
    """

    __tablename__ = "plan_versions"
    __table_args__ = (
        # An alternative (P_strict of E6) shares the version of the plan it
        # replaces, so only the main rows are unique per trip and version.
        Index(
            "uq_plan_versions_trip_version",
            "trip_id",
            "version",
            unique=True,
            postgresql_where=text("alternative_of IS NULL"),
        ),
        Index("ix_plan_versions_trip_input_hash", "trip_id", "input_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE")
    )
    version: Mapped[int]
    input_hash: Mapped[str] = mapped_column(String(64))
    plan_hash: Mapped[str] = mapped_column(String(12))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by_sub: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    alternative_of: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="CASCADE")
    )
