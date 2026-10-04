"""Check-in ORM model."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class TripCheckin(Base):
    """Where one profile stays on a trip and in which room."""

    __tablename__ = "trip_checkins"

    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True
    )
    # String FK: no import of the profiles domain's models.
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    accommodation: Mapped[str] = mapped_column(String(200))
    room: Mapped[str | None] = mapped_column(String(20))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
