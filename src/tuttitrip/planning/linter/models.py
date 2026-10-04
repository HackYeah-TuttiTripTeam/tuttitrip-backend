"""Pasted documents: plan and offer texts that the worker reads by id."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.planning.linter.schemas import MAX_DOCUMENT_CHARS, DocumentKind
from tuttitrip.shared.db.base import Base


class PastedDocument(Base):
    """A text pasted for one trip; removed together with the trip."""

    __tablename__ = "pasted_documents"
    __table_args__ = (
        CheckConstraint(
            "kind IN ({})".format(", ".join(f"'{k.value}'" for k in DocumentKind)),
            name="kind",
        ),
        CheckConstraint(
            f"char_length(text) BETWEEN 1 AND {MAX_DOCUMENT_CHARS}", name="text_length"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
