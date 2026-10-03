"""Invitation ORM model."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class TripInvitation(Base):
    """An invitation link: a join permission with a use limit and an expiry.

    Only the SHA-256 of the token is stored; the plain token exists once, in
    the creation response.
    """

    __tablename__ = "trip_invitations"
    __table_args__ = (
        CheckConstraint("max_uses >= 1", name="max_uses_positive"),
        CheckConstraint("uses >= 0 AND uses <= max_uses", name="uses_in_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by_sub: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    max_uses: Mapped[int]
    uses: Mapped[int] = mapped_column(default=0, server_default="0")
    revoked_at: Mapped[datetime | None]
