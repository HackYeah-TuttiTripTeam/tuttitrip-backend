"""What was exported to Google, so a second export updates instead of duplicating."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class GoogleExport(Base):
    """The calendar or the document one user made from a trip's plan.

    ``external_id`` is the Google calendar id or the Drive file id. ``event_ids``
    lists the events in the calendar, to remove the ones a newer version dropped.
    No token is stored here.
    """

    __tablename__ = "google_exports"
    __table_args__ = (
        UniqueConstraint("trip_id", "user_sub", "kind"),
        CheckConstraint("kind IN ('calendar', 'drive')", name="kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE")
    )
    user_sub: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(255))
    web_link: Mapped[str | None]
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="SET NULL")
    )
    event_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
