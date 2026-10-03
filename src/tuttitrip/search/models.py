"""Embedding ORM model (pgvector)."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class Embedding(Base):
    """A vector for one piece of content; written by the worker role."""

    __tablename__ = "embeddings"
    __table_args__ = (Index("ix_embeddings_source", "source_kind", "source_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_kind: Mapped[str] = mapped_column(String(50))
    source_id: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(200))
    # No fixed dimension yet: add one (and an HNSW index) once the model is chosen.
    embedding: Mapped[list[float]] = mapped_column(Vector())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
