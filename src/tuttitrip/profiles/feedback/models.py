"""Place rating and veto ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.planning.plans.schemas import ReasonCode
from tuttitrip.profiles.feedback.schemas import RatingValue
from tuttitrip.shared.db.base import Base


def _in(column: str, values: type[RatingValue | ReasonCode]) -> str:
    return f"{column} IN ({', '.join(f"'{v.value}'" for v in values)})"


class PlaceRating(Base):
    """One person's vote on one catalog place (one row per profile and place)."""

    __tablename__ = "place_ratings"
    __table_args__ = (
        UniqueConstraint("profile_id", "place_id"),
        CheckConstraint(_in("value", RatingValue), name="value"),
        CheckConstraint(_in("reason_code", ReasonCode), name="reason_code"),
        CheckConstraint(
            "(value = 'dont_want') = (reason_code IS NOT NULL)",
            name="reason_iff_dont_want",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # String FKs: no imports of other domains' models.
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE")
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("places.id", ondelete="CASCADE")
    )
    value: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str | None] = mapped_column(String(32))
    updated_by_sub: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class PlaceVeto(Base):
    """A hard block (E0) of a place by one person; revoked, never deleted."""

    __tablename__ = "place_vetoes"
    __table_args__ = (
        # At most one veto in force per person and place; revoked ones stay as history.
        Index(
            "uq_place_vetoes_active",
            "profile_id",
            "place_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE")
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("places.id", ondelete="CASCADE")
    )
    created_by_sub: Mapped[str] = mapped_column(String(255))
    on_behalf: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None]
