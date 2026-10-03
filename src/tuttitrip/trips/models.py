"""Trip ORM models."""

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Numeric,
    String,
    Time,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base
from tuttitrip.trips.schemas import TripRole


class Trip(Base):
    """A trip created by one organizer (Auth0 ``sub``), who is its host."""

    __tablename__ = "trips"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="dates"),
        CheckConstraint("day_start < day_end", name="day_window"),
        CheckConstraint("budget_total_min <= budget_total_max", name="budget_total"),
        CheckConstraint("budget_day_min <= budget_day_max", name="budget_day"),
        CheckConstraint("budget_flex_pct BETWEEN 0 AND 50", name="budget_flex"),
        CheckConstraint("fairness_alpha BETWEEN 0 AND 3", name="fairness_alpha"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_sub: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(200))
    destination: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    start_date: Mapped[date | None] = mapped_column()
    end_date: Mapped[date | None] = mapped_column()
    # Time of day `T` for the solver; an evening outing is e.g. 18:00-23:00.
    day_start: Mapped[time] = mapped_column(Time, server_default=text("'09:00'"))
    day_end: Mapped[time] = mapped_column(Time, server_default=text("'19:00'"))
    # Plain text, no FK: the cities table comes with another issue.
    city_slug: Mapped[str | None] = mapped_column(String(100))
    currency: Mapped[str | None] = mapped_column(String(3))
    budget_total_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    budget_total_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    budget_day_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    budget_day_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # `flex` of E6 in percent: B_max = B_do * (1 + flex).
    budget_flex_pct: Mapped[int] = mapped_column(server_default=text("0"))
    # Group goal alpha of E5.
    fairness_alpha: Mapped[float] = mapped_column(Float, server_default=text("1"))


class TripMember(Base):
    """A user's role on a trip (object-level access; the owner is the host)."""

    __tablename__ = "trip_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ({})".format(", ".join(f"'{r.value}'" for r in TripRole)),
            name="role",
        ),
    )

    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True
    )
    user_sub: Mapped[str] = mapped_column(String(255), primary_key=True, index=True)
    role: Mapped[TripRole] = mapped_column(
        Enum(
            TripRole,
            name="trip_role",
            native_enum=False,
            length=10,
            values_callable=lambda roles: [r.value for r in roles],
        )
    )
    added_at: Mapped[datetime] = mapped_column(server_default=func.now())
