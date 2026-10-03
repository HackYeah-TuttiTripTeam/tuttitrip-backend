"""Trip ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base
from tuttitrip.trips.schemas import TripRole


class Trip(Base):
    """A trip created by one organizer (Auth0 ``sub``), who is its host."""

    __tablename__ = "trips"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_sub: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(200))
    destination: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TripMember(Base):
    """A user's role on a trip (object-level access; the owner is the host)."""

    __tablename__ = "trip_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ({})".format(", ".join(f"'{r.value}'" for r in TripRole)),
            name="role",
        ),
    )

    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True
    )
    user_sub: Mapped[str] = mapped_column(String(255), primary_key=True, index=True)
    role: Mapped[TripRole] = mapped_column(
        Enum(
            TripRole,
            name="trip_role",
            native_enum=False,
            length=10,
            values_callable=lambda roles: [r.value for r in roles],
        )
    )
    added_at: Mapped[datetime] = mapped_column(server_default=func.now())
