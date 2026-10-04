"""Sample trip: the flag on trips and the per-account mark.

Revision ID: d217a5e9c1b3
Revises: c4d71e9b0a52
Create Date: 2026-10-04 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d217a5e9c1b3"
down_revision: str | None = "c4d71e9b0a52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.add_column(
        "trips",
        sa.Column(
            "is_sample", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.create_table(
        "sample_trip_grants",
        sa.Column("user_sub", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_sub", name=op.f("pk_sample_trip_grants")),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_table("sample_trip_grants")
    op.drop_column("trips", "is_sample")
