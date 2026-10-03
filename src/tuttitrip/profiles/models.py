"""Profile ORM model."""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class Profile(Base):
    """A person on a trip; not an account, just a profile owned by the organizer."""

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # String FK: no import of the trips domain's models.
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(100))
    age_group: Mapped[str] = mapped_column(String(16), default="adult")
    weight: Mapped[float] = mapped_column(default=1.0)
