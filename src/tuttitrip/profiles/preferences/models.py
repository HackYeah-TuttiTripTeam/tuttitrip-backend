"""Preferences ORM model (1:1 with a profile)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class ProfilePreferences(Base):
    """What one person wants; the JSONB columns hold validated DTO dumps."""

    __tablename__ = "profile_preferences"

    # String FK: the row is deleted with its profile.
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    interests: Mapped[dict[str, float]] = mapped_column(JSONB)
    importance_pool: Mapped[dict[str, int]] = mapped_column(JSONB)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB)
    diet: Mapped[dict[str, Any]] = mapped_column(JSONB)
    example_places: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    min_tags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    updated_by_sub: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
