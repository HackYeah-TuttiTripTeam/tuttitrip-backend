"""Plan versions.

Revision ID: 306bfea56fc5
Revises: 0f8ab2d0e440, 65807aa11765
Create Date: 2026-10-04 03:36:24.618025
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "306bfea56fc5"
down_revision: str | Sequence[str] | None = ("0f8ab2d0e440", "65807aa11765")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "plan_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_hash", sa.String(length=12), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_sub", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_plan_versions_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_versions")),
        sa.UniqueConstraint("trip_id", "version", name="uq_plan_versions_trip_version"),
    )
    op.create_index(
        "ix_plan_versions_trip_input_hash",
        "plan_versions",
        ["trip_id", "input_hash"],
        unique=False,
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_index("ix_plan_versions_trip_input_hash", table_name="plan_versions")
    op.drop_table("plan_versions")
