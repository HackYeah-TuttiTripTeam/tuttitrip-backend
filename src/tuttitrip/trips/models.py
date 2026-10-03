"""Trip ORM model."""

import uuid
from datetime import datetime

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class Trip(Base):
    """A trip owned by one organizer (Auth0 ``sub``)."""

    __tablename__ = "trips"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_sub: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(200))
    destination: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
