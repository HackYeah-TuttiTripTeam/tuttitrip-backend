"""Checks of pasted plans: the worker's parse, the host's picks and the report."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class PasteCheck(Base):
    """One pasted plan being checked; its id is the pasted document's id.

    ``parsed`` is the worker's output, kept when the job succeeded (the catalog
    may change later, the parse does not). ``picks`` maps an item index to the
    catalog place the host chose. ``report`` is the lint report, stored on the
    first read after the job and recomputed after every pick.
    """

    __tablename__ = "paste_checks"

    id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pasted_documents.id", ondelete="CASCADE"), primary_key=True
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str] = mapped_column(String(300))
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    picks: Mapped[dict[str, str]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(50))
    job_failed: Mapped[bool] = mapped_column(
        default=False, server_default=text("false")
    )
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
