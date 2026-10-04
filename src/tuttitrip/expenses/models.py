"""Expense ORM models."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tuttitrip.expenses.schemas import ExpenseCategory, SplitMethod
from tuttitrip.shared.db.base import Base


def _in(column: str, values: type[SplitMethod | ExpenseCategory]) -> str:
    return f"{column} IN ({', '.join(f"'{v.value}'" for v in values)})"


class Expense(Base):
    """One payment made by a person during the trip, shared by some people."""

    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("trip_amount > 0", name="trip_amount_positive"),
        CheckConstraint("rate_source IN ('nbp', 'manual')", name="rate_source"),
        CheckConstraint(_in("split_method", SplitMethod), name="split_method"),
        CheckConstraint(
            _in("category", ExpenseCategory),
            name="category",
        ),
        Index("ix_expenses_trip_id_spent_on", "trip_id", "spent_on"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    # Not CASCADE: removing a payer must not silently drop expenses (the API
    # answers 409 first). Deferred to commit because plain NO ACTION is checked
    # while deleting a whole trip cascades to profiles and expenses in an order
    # Postgres picks (verified: it fails on expense_shares); deferred, the trip
    # delete passes and a profile delete is still refused at commit.
    payer_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", deferrable=True, initially="DEFERRED"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    # `amount` converted to the trip's currency (equal to `amount` when the same).
    trip_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    # Set only for a foreign currency; frozen at save (see `rates.py`).
    rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    rate_source: Mapped[str | None] = mapped_column(String(8))
    rate_table: Mapped[str | None] = mapped_column(String(80))
    rate_date: Mapped[date | None]
    description: Mapped[str] = mapped_column(String(500), default="")
    spent_on: Mapped[date]
    category: Mapped[ExpenseCategory | None] = mapped_column(String(16))
    split_method: Mapped[SplitMethod] = mapped_column(String(16))
    created_by_sub: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    shares: Mapped[list[ExpenseShare]] = relationship(
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="ExpenseShare.profile_id",
    )


class ExpenseShare(Base):
    """A participant of an expense; a person who is left out has no row."""

    __tablename__ = "expense_shares"
    __table_args__ = (CheckConstraint("value > 0", name="value_positive"),)

    expense_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expenses.id", ondelete="CASCADE"), primary_key=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", deferrable=True, initially="DEFERRED"),
        primary_key=True,
        index=True,
    )
    # Empty for `equal`, percent for `percent`, weight for `weights`.
    value: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
