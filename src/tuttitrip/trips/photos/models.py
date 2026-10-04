"""Photo ORM model."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class TripPhoto(Base):
    """One photo of a trip: the resized image and its thumbnail, as ``bytea``.

    Lists load the row without ``image`` (``defer``), only the image route
    reads it. The uploaded file name is never stored.
    """

    __tablename__ = "trip_photos"
    __table_args__ = (Index("ix_trip_photos_trip_created", "trip_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE")
    )
    # NULL once the author left the trip: the photo stays, unattributed.
    author_sub: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(20))
    size_bytes: Mapped[int]
    thumbnail_type: Mapped[str] = mapped_column(String(20))
    thumbnail: Mapped[bytes] = mapped_column(LargeBinary)
    image: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
