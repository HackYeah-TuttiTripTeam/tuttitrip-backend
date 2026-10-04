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


def downgrade() -> None:
    """Revert this revision."""
    op.drop_constraint(op.f("ck_expenses_rate_source"), "expenses", type_="check")
    op.drop_constraint(
        op.f("ck_expenses_trip_amount_positive"), "expenses", type_="check"
    )
    op.drop_column("expenses", "rate_date")
    op.drop_column("expenses", "rate_table")
    op.drop_column("expenses", "rate_source")
    op.drop_column("expenses", "rate")
    op.drop_column("expenses", "trip_amount")
