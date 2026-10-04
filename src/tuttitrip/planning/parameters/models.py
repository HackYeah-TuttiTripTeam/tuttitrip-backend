"""Versions of the algorithm parameters."""

from datetime import datetime
from typing import Any

from sqlalchemy import String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class ParameterVersion(Base):
    """One complete set of parameters, immutable once stored.

    Version 0 is the built-in default and has no row. A plan records the
    version it was computed with, so changing the parameters never touches old
    plans.
    """

    __tablename__ = "planning_parameter_versions"

    version: Mapped[int] = mapped_column(primary_key=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    created_by_sub: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
