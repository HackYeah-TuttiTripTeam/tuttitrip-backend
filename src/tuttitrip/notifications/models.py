"""Notification ORM model: the outbox behind the inbox, counter and stream."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class Notification(Base):
    """A notification for one user.

    ``user_sub`` is an Auth0 id like in ``trip_members`` (no users table, no
    FK). ``type`` is plain text without a CHECK, so a new type needs no
    migration. A trigger on insert sends ``pg_notify('notifications', ...)``
    with the ``id`` and ``user_sub`` only (see the migration).
    """

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_sub", "dedupe_key", name="uq_notifications_dedupe"),
        Index("ix_notifications_user_created", "user_sub", text("created_at DESC")),
        Index(
            "ix_notifications_user_unread",
            "user_sub",
            postgresql_where=text("read_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_sub: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(64))
    trip_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE")
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    dedupe_key: Mapped[str | None] = mapped_column(String(255))
    read_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
