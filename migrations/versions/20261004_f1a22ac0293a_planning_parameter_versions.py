"""Planning parameter versions.

Revision ID: f1a22ac0293a
Revises: 6c73942873a4
Create Date: 2026-10-04 04:40:13.341646
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1a22ac0293a"
down_revision: str | None = "6c73942873a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "planning_parameter_versions",
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_sub", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_planning_parameter_versions")),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_table("planning_parameter_versions")
