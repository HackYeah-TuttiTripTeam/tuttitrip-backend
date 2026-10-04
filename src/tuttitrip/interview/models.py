"""Interview ORM models."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Enum, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.interview.schemas import KnowledgeField, SessionStatus
from tuttitrip.shared.db.base import Base


class InterviewSession(Base):
    """One interview of a trip; its id is the AG-UI ``threadId``."""

    __tablename__ = "interview_sessions"
    # One open session per trip: creating it twice returns the same one.
    __table_args__ = (
        Index(
            "uq_interview_sessions_open_trip",
            "trip_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # No plain index: the partial unique index above serves every lookup.
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE")
    )
    status: Mapped[SessionStatus] = mapped_column(
        Enum(
            SessionStatus,
            name="interview_status",
            native_enum=False,
            length=10,
            values_callable=lambda items: [i.value for i in items],
        ),
        server_default=SessionStatus.OPEN.value,
    )
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
    # `ModelMessagesTypeAdapter.dump_python(..., mode="json")` of the whole run.
    history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )


class AssistantValue(Base):
    """Digest of a value the assistant wrote, to tell it from a host's edit."""

    __tablename__ = "interview_assistant_values"
    __table_args__ = (
        Index(
            "uq_interview_assistant_values_ref",
            "trip_id",
            "field",
            "profile_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE")
    )
    field: Mapped[KnowledgeField] = mapped_column(
        Enum(
            KnowledgeField,
            name="knowledge_field",
            native_enum=False,
            length=20,
            values_callable=lambda items: [i.value for i in items],
        )
    )
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE")
    )
    digest: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
