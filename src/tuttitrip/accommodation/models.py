"""Accommodation ORM models: requirements, their version, searches and offers."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.accommodation.logic.keys import Platform, RequirementKind
from tuttitrip.shared.db.base import Base

_KINDS = ", ".join(f"'{kind.value}'" for kind in RequirementKind)
_PLATFORMS = ", ".join(f"'{p.value}'" for p in Platform)


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
    __table_args__ = (CheckConstraint(f"platform IN ({_PLATFORMS})", name="platform"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[Platform] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(Text)
    params: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    actor_sub: Mapped[str] = mapped_column(String(255))
    opened_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AccommodationOffer(Base):
    """A pasted lodging offer for some nights, with what the worker found in it.

    The three-state result is computed on read from ``features`` (host answers),
    ``evidence`` (worker quotes) and the link, against the current requirements.
    """

    __tablename__ = "accommodation_offers"
    __table_args__ = (
        CheckConstraint(f"platform IN ({_PLATFORMS})", name="platform"),
        CheckConstraint("cardinality(nights) >= 1", name="nights"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pasted_documents.id", ondelete="CASCADE"), index=True
    )
    nights: Mapped[list[date]] = mapped_column(ARRAY(Date))
    url: Mapped[str | None] = mapped_column(Text)
    platform: Mapped[Platform | None] = mapped_column(String(16))
    features: Mapped[dict[str, Any]] = mapped_column(JSONB)
    requirements_version: Mapped[int]
    requested_keys: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    job_id: Mapped[str | None] = mapped_column(String(300))
    evidence: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    job_failed: Mapped[bool] = mapped_column(server_default=text("false"))
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
