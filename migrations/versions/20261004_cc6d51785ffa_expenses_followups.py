"""Expenses follow-ups: foreign-currency rate, payments, settlement state, evidence.

Revision ID: cc6d51785ffa
Revises: b28a37ba42ff
Create Date: 2026-10-04 02:55:59.180093
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cc6d51785ffa"
down_revision: str | None = "b28a37ba42ff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    # --- #85: amount in the trip currency and the frozen exchange rate ---------
    op.add_column(
        "expenses",
        sa.Column("trip_amount", sa.Numeric(precision=12, scale=2), nullable=True),
    )
    # Existing expenses were all in the trip currency.
    op.execute("UPDATE expenses SET trip_amount = amount")
    op.alter_column("expenses", "trip_amount", nullable=False)
    op.add_column(
        "expenses", sa.Column("rate", sa.Numeric(precision=18, scale=8), nullable=True)
    )
    op.add_column(
        "expenses", sa.Column("rate_source", sa.String(length=8), nullable=True)
    )
    op.add_column(
        "expenses", sa.Column("rate_table", sa.String(length=80), nullable=True)
    )
    op.add_column("expenses", sa.Column("rate_date", sa.Date(), nullable=True))
    op.create_check_constraint(
        op.f("ck_expenses_trip_amount_positive"), "expenses", "trip_amount > 0"
    )
    op.create_check_constraint(
        op.f("ck_expenses_rate_source"),
        "expenses",
        "rate_source IN ('nbp', 'manual')",
    )

    # --- #88: payments marked as paid and the closed state of a settlement ------
    op.create_table(
        "settlement_payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("from_profile_id", sa.Uuid(), nullable=False),
        sa.Column("to_profile_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("marked_by_sub", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount > 0", name=op.f("ck_settlement_payments_amount_positive")
        ),
        sa.CheckConstraint(
            "from_profile_id <> to_profile_id",
            name=op.f("ck_settlement_payments_distinct_people"),
        ),
        sa.ForeignKeyConstraint(
            ["from_profile_id"],
            ["profiles.id"],
            name=op.f("fk_settlement_payments_from_profile_id_profiles"),
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["to_profile_id"],
            ["profiles.id"],
            name=op.f("fk_settlement_payments_to_profile_id_profiles"),
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_settlement_payments_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlement_payments")),
    )
    op.create_index(
        op.f("ix_settlement_payments_trip_id"),
        "settlement_payments",
        ["trip_id"],
        unique=False,
    )
    op.create_index(
        "ix_settlement_payments_trip_id_paid_on",
        "settlement_payments",
        ["trip_id", "paid_on"],
        unique=False,
    )
    op.create_table(
        "trip_settlements",
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("closed_by_sub", sa.String(length=255), nullable=False),
        sa.Column(
            "closed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_trip_settlements_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("trip_id", name=op.f("pk_trip_settlements")),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_table("trip_settlements")
    op.drop_index("ix_settlement_payments_trip_id_paid_on", "settlement_payments")
    op.drop_index(op.f("ix_settlement_payments_trip_id"), "settlement_payments")
    op.drop_table("settlement_payments")
    op.drop_constraint(op.f("ck_expenses_rate_source"), "expenses", type_="check")
    op.drop_constraint(
        op.f("ck_expenses_trip_amount_positive"), "expenses", type_="check"
    )
    op.drop_column("expenses", "rate_date")
    op.drop_column("expenses", "rate_table")
    op.drop_column("expenses", "rate_source")
    op.drop_column("expenses", "rate")
    op.drop_column("expenses", "trip_amount")
