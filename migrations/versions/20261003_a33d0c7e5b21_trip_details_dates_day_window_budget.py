"""Trip details: dates, day window, city, budget range with flex, alpha.

CHECK constraints are written by hand: autogenerate does not detect CHECKs
added to an existing table. New columns are nullable or have defaults, so
existing trips stay valid.

Revision ID: a33d0c7e5b21
Revises: 6801abbf6bbb
Create Date: 2026-10-03 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a33d0c7e5b21"
down_revision: str | None = "6801abbf6bbb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKS: tuple[tuple[str, str], ...] = (
    ("dates", "end_date >= start_date"),
    ("day_window", "day_start < day_end"),
    ("budget_total", "budget_total_min <= budget_total_max"),
    ("budget_day", "budget_day_min <= budget_day_max"),
    ("budget_flex", "budget_flex_pct BETWEEN 0 AND 50"),
    ("fairness_alpha", "fairness_alpha BETWEEN 0 AND 3"),
)


def upgrade() -> None:
    """Add the trip detail columns and their CHECK constraints."""
    op.add_column("trips", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("trips", sa.Column("end_date", sa.Date(), nullable=True))
    op.add_column(
        "trips",
        sa.Column(
            "day_start", sa.Time(), server_default=sa.text("'09:00'"), nullable=False
        ),
    )
    op.add_column(
        "trips",
        sa.Column(
            "day_end", sa.Time(), server_default=sa.text("'19:00'"), nullable=False
        ),
    )
    op.add_column("trips", sa.Column("city_slug", sa.String(length=64), nullable=True))
    op.add_column("trips", sa.Column("currency", sa.String(length=3), nullable=True))
    for name in (
        "budget_total_min",
        "budget_total_max",
        "budget_day_min",
        "budget_day_max",
    ):
        op.add_column("trips", sa.Column(name, sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "trips",
        sa.Column(
            "budget_flex_pct",
            sa.Integer(),
            server_default=sa.text("10"),
            nullable=False,
        ),
    )
    op.add_column(
        "trips",
        sa.Column(
            "fairness_alpha", sa.Float(), server_default=sa.text("1"), nullable=False
        ),
    )
    for name, condition in CHECKS:
        op.create_check_constraint(name, "trips", condition)


def downgrade() -> None:
    """Drop the CHECK constraints and the trip detail columns."""
    for name, _ in CHECKS:
        op.drop_constraint(name, "trips", type_="check")
    for column in (
        "fairness_alpha",
        "budget_flex_pct",
        "budget_day_max",
        "budget_day_min",
        "budget_total_max",
        "budget_total_min",
        "currency",
        "city_slug",
        "day_end",
        "day_start",
        "end_date",
        "start_date",
    ):
        op.drop_column("trips", column)
