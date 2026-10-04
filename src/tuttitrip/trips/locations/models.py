"""Location ORM models: the consent, and the single last position per person."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class TripLocationConsent(Base):
    """A person agreed to share their location on one trip, until ``until``.

    No row means no consent (the default). Deleted when the person stops,
    when it lapses and when they leave the trip.
    """

    __tablename__ = "trip_location_consents"

    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True
    )
    # String FK: no import of the profiles domain's models.
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    granted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    until: Mapped[datetime]


class TripLocation(Base):
    """The last known position of one person on one trip (no history).

    Each update overwrites the row. It is never returned after ``expires_at``
    and is deleted on the next access to the trip's locations.
    """

    __tablename__ = "trip_locations"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        CheckConstraint("accuracy_m IS NULL OR accuracy_m >= 0", name="accuracy_range"),
    )

    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    latitude: Mapped[float]
    longitude: Mapped[float]
    accuracy_m: Mapped[float | None]
    recorded_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
