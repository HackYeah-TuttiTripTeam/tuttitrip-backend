"""Accommodation ORM models: requirements of a trip and their version."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.accommodation.logic.keys import Platform, RequirementKind
from tuttitrip.shared.db.base import Base

_KINDS = ", ".join(f"'{kind.value}'" for kind in RequirementKind)


class AccommodationRequirement(Base):
    """One lodging requirement (amenity, platform or max distance) of a trip."""

    __tablename__ = "accommodation_requirements"
    __table_args__ = (
        UniqueConstraint("trip_id", "kind", "key"),
        CheckConstraint(f"kind IN ({_KINDS})", name="kind"),
        CheckConstraint(
            "(kind = 'distance') = (max_distance_m IS NOT NULL)", name="distance"
        ),
        CheckConstraint("max_distance_m > 0", name="max_distance_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE")
    )
    kind: Mapped[RequirementKind] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(64))
    hard: Mapped[bool] = mapped_column(Boolean)
    max_distance_m: Mapped[int | None] = mapped_column()


class RequirementsVersion(Base):
    """Change counter of a trip's requirements (absent row = version 0)."""

    __tablename__ = "accommodation_requirements_versions"

    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(server_default=text("0"))


class SearchOpening(Base):
    """A host approved opening a platform search.

    Append-only by API convention: no endpoint updates or deletes a row.
    """

    __tablename__ = "accommodation_search_openings"
    __table_args__ = (
        CheckConstraint(
            "platform IN ({})".format(", ".join(f"'{p.value}'" for p in Platform)),
            name="platform",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[Platform] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(Text)
    params: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    actor_sub: Mapped[str] = mapped_column(String(255))
    opened_at: Mapped[datetime] = mapped_column(server_default=func.now())
