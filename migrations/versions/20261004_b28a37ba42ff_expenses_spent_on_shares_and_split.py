"""Expenses: spent_on, author, split method, category and per-person shares.

Revision ID: b28a37ba42ff
Revises: 6b826a460bb7
Create Date: 2026-10-04 01:56:26.297779
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b28a37ba42ff"
down_revision: str | Sequence[str] | None = "6b826a460bb7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "expense_shares",
        sa.Column("expense_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.CheckConstraint("value > 0", name=op.f("ck_expense_shares_value_positive")),
        sa.ForeignKeyConstraint(
            ["expense_id"],
            ["expenses.id"],
            name=op.f("fk_expense_shares_expense_id_expenses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name=op.f("fk_expense_shares_profile_id_profiles"),
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint(
            "expense_id", "profile_id", name=op.f("pk_expense_shares")
        ),
    )
    op.create_index(
        op.f("ix_expense_shares_profile_id"),
        "expense_shares",
        ["profile_id"],
        unique=False,
    )
    op.add_column("expenses", sa.Column("spent_on", sa.Date(), nullable=True))
    op.add_column(
        "expenses", sa.Column("category", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "expenses", sa.Column("split_method", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "expenses", sa.Column("created_by_sub", sa.String(length=255), nullable=True)
    )
    # Rows that predate the columns: spent the (UTC) day they were added, shared
    # equally by the payer and everyone else on the trip, authored by the trip's
    # host. 'unknown' only when a trip has no host row (should not happen), because
    # the column is NOT NULL.
    op.execute(
        """
        UPDATE expenses e SET
            spent_on = (e.created_at AT TIME ZONE 'UTC')::date,
            split_method = 'equal',
            created_by_sub = COALESCE(
                (SELECT m.user_sub FROM trip_members m
                 WHERE m.trip_id = e.trip_id AND m.role = 'host' LIMIT 1),
                'unknown'
            )
        """
    )
    op.execute(
        """
        INSERT INTO expense_shares (expense_id, profile_id)
        SELECT e.id, e.payer_profile_id FROM expenses e
        UNION
        SELECT e.id, p.id FROM expenses e JOIN profiles p ON p.trip_id = e.trip_id
        """
    )
    for column in ("spent_on", "split_method", "created_by_sub"):
        op.alter_column("expenses", column, nullable=False)
    op.execute(
        "ALTER TABLE expenses ADD CONSTRAINT ck_expenses_amount_positive "
        "CHECK (amount > 0) NOT VALID"
    )
    op.create_check_constraint(
        "split_method",
        "expenses",
        "split_method IN ('equal', 'percent', 'weights')",
    )
    op.create_check_constraint(
        "category",
        "expenses",
        "category IN ('food', 'transport', 'lodging', 'activities', 'shopping', "
        "'other')",
    )
    op.create_index(
        op.f("ix_expenses_payer_profile_id"),
        "expenses",
        ["payer_profile_id"],
        unique=False,
    )
    op.create_index(
        "ix_expenses_trip_id_spent_on",
        "expenses",
        ["trip_id", "spent_on"],
        unique=False,
    )
    op.drop_constraint(
        op.f("fk_expenses_payer_profile_id_profiles"), "expenses", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_expenses_payer_profile_id_profiles"),
        "expenses",
        "profiles",
        ["payer_profile_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_constraint(
        op.f("fk_expenses_payer_profile_id_profiles"), "expenses", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_expenses_payer_profile_id_profiles"),
        "expenses",
        "profiles",
        ["payer_profile_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(op.f("ck_expenses_category"), "expenses", type_="check")
    op.drop_constraint(op.f("ck_expenses_split_method"), "expenses", type_="check")
    op.drop_constraint(op.f("ck_expenses_amount_positive"), "expenses", type_="check")
    op.drop_index("ix_expenses_trip_id_spent_on", table_name="expenses")
    op.drop_index(op.f("ix_expenses_payer_profile_id"), table_name="expenses")
    op.drop_column("expenses", "created_by_sub")
    op.drop_column("expenses", "split_method")
    op.drop_column("expenses", "category")
    op.drop_column("expenses", "spent_on")
    op.drop_index(op.f("ix_expense_shares_profile_id"), table_name="expense_shares")
    op.drop_table("expense_shares")
