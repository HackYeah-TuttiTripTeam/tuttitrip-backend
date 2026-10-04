"""Expenses follow-ups: foreign-currency rate, payments, settlement state, evidence.

Revision ID: cc6d51785ffa
Revises: b9e249a27e27
Create Date: 2026-10-04 02:55:59.180093
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cc6d51785ffa"
down_revision: str | None = "b9e249a27e27"
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

    # --- #87: receipt images and draft expenses --------------------------------
    op.create_table(
        "expense_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("media_type", sa.String(length=20), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_by_sub", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_expense_evidence_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expense_evidence")),
    )
    op.create_index(
        op.f("ix_expense_evidence_trip_id"), "expense_evidence", ["trip_id"]
    )
    op.create_index(
        op.f("ix_expense_evidence_delete_after"), "expense_evidence", ["delete_after"]
    )
    op.add_column(
        "expenses",
        sa.Column(
            "status",
            sa.String(length=10),
            server_default="confirmed",
            nullable=False,
        ),
    )
    op.add_column("expenses", sa.Column("evidence_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        op.f("uq_expenses_evidence_id"), "expenses", ["evidence_id"]
    )
    op.create_foreign_key(
        op.f("fk_expenses_evidence_id_expense_evidence"),
        "expenses",
        "expense_evidence",
        ["evidence_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_expenses_status"), "expenses", "status IN ('draft', 'confirmed')"
    )
    op.create_check_constraint(
        op.f("ck_expenses_trip_amount_priced"),
        "expenses",
        "status = 'draft' OR trip_amount IS NOT NULL",
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
    op.drop_constraint(
        op.f("ck_expenses_trip_amount_priced"), "expenses", type_="check"
    )
    op.drop_constraint(op.f("ck_expenses_status"), "expenses", type_="check")
    op.drop_constraint(
        op.f("fk_expenses_evidence_id_expense_evidence"), "expenses", type_="foreignkey"
    )
    op.drop_constraint(op.f("uq_expenses_evidence_id"), "expenses", type_="unique")
    op.drop_column("expenses", "evidence_id")
    op.drop_column("expenses", "status")
    op.drop_index(op.f("ix_expense_evidence_delete_after"), "expense_evidence")
    op.drop_index(op.f("ix_expense_evidence_trip_id"), "expense_evidence")
    op.drop_table("expense_evidence")
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
