"""Web research columns of places: description, child_friendly, enriched_at.

Revision ID: 7c2e9b41d0a3
Revises: ab1dd832af00
Create Date: 2026-10-04 08:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c2e9b41d0a3"
down_revision: str | None = "ab1dd832af00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.add_column("places", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("places", sa.Column("child_friendly", sa.Boolean(), nullable=True))
    op.add_column(
        "places", sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_column("places", "enriched_at")
    op.drop_column("places", "child_friendly")
    op.drop_column("places", "description")
