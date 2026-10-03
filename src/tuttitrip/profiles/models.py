"""Profile ORM model."""

import uuid
from datetime import time

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.profiles.logic.age_defaults import age_group_for
from tuttitrip.profiles.schemas import AgeGroup
from tuttitrip.shared.db.base import Base


class Profile(Base):
    """A person on a trip; an account is optional (``user_sub``)."""

    __tablename__ = "profiles"
    # NULLs never collide, so any number of profiles without an account is fine.
    __table_args__ = (
        UniqueConstraint("trip_id", "user_sub"),
        CheckConstraint("weight > 0", name="weight_positive"),
        CheckConstraint("floor BETWEEN 0 AND 100", name="floor_range"),
        CheckConstraint(
            "stairs_sensitivity BETWEEN 0 AND 1", name="stairs_sensitivity_range"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # String FK: no import of the trips domain's models.
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(100))
    age: Mapped[int]
    user_sub: Mapped[str | None] = mapped_column(String(255))
    weight: Mapped[float] = mapped_column(default=1.0)
    segment_km: Mapped[float]
    daily_km: Mapped[float]
    active_min: Mapped[int]
    stairs_sensitivity: Mapped[float]
    queue_patience_min: Mapped[int]
    nap_start: Mapped[time | None]
    nap_minutes: Mapped[int] = mapped_column(default=0)
    floor: Mapped[int] = mapped_column(default=30)

    @property
    def age_group(self) -> AgeGroup:
        """Age group, always computed from the age.

        Returns:
            The group whose defaults apply to this person.
        """
        return age_group_for(self.age)
