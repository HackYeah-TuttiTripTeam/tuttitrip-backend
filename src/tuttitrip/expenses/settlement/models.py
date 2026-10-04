"""Settlement ORM models."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class SettlementPayment(Base):
    """A transfer of the settlement that someone marked as paid (in part or whole)."""

    __tablename__ = "settlement_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("from_profile_id <> to_profile_id", name="distinct_people"),
        Index("ix_settlement_payments_trip_id_paid_on", "trip_id", "paid_on"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    # Deferred like the expense FKs, so deleting a whole trip goes through.
    from_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", deferrable=True, initially="DEFERRED")
    )
    to_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", deferrable=True, initially="DEFERRED")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    paid_on: Mapped[date]
    marked_by_sub: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TripSettlement(Base):
    """A closed settlement; no row means the settlement is open."""

    __tablename__ = "trip_settlements"

    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True
    )
    closed_by_sub: Mapped[str] = mapped_column(String(255))
    closed_at: Mapped[datetime] = mapped_column(server_default=func.now())
