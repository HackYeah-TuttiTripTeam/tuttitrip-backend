"""Accommodation search openings.

Revision ID: 243e9787efa9
Revises: 2aba684f6abc
Create Date: 2026-10-04 01:14:41.461655
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "243e9787efa9"
down_revision: str | None = "2aba684f6abc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "accommodation_search_openings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actor_sub", sa.String(length=255), nullable=False),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "platform IN ('airbnb', 'booking')",
            name=op.f("ck_accommodation_search_openings_platform"),
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_accommodation_search_openings_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accommodation_search_openings")),
    )
    op.create_index(
        op.f("ix_accommodation_search_openings_trip_id"),
        "accommodation_search_openings",
        ["trip_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_index(
        op.f("ix_accommodation_search_openings_trip_id"),
        table_name="accommodation_search_openings",
    )
    op.drop_table("accommodation_search_openings")
