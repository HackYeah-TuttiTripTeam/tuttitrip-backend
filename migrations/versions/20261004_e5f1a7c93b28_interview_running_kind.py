"""Interview run guard: what holds the session (text turn or voice call).

Revision ID: e5f1a7c93b28
Revises: 093e32d28baf
Create Date: 2026-10-04 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f1a7c93b28"
down_revision: str | None = "093e32d28baf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.add_column(
        "interview_sessions",
        sa.Column("running_kind", sa.String(length=5), nullable=True),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_column("interview_sessions", "running_kind")
