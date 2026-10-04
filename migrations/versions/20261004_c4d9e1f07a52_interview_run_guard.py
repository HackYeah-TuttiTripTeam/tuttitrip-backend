"""Interview run guard and voice time used.

Revision ID: c4d9e1f07a52
Revises: 65807aa11765
Create Date: 2026-10-04 12:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d9e1f07a52"
down_revision: str | None = "65807aa11765"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.add_column(
        "interview_sessions",
        sa.Column("running_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "interview_sessions",
        sa.Column("voice_seconds", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_column("interview_sessions", "voice_seconds")
    op.drop_column("interview_sessions", "running_until")
