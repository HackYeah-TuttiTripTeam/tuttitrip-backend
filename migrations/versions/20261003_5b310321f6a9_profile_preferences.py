"""Profile preferences: interests, importance pool, constraints, diet.

Revision ID: 5b310321f6a9
Revises: 2647fc89c3ae
Create Date: 2026-10-03 21:19:35.268096
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5b310321f6a9"
down_revision: str | None = "2647fc89c3ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "profile_preferences",
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("interests", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "importance_pool", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "constraints", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("diet", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "example_places", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("min_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by_sub", sa.String(length=255), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name=op.f("fk_profile_preferences_profile_id_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("profile_id", name=op.f("pk_profile_preferences")),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_table("profile_preferences")
