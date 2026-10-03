"""Enable pgvector.

Revision ID: ccb417c53a87
Revises: 041902abed20
Create Date: 2026-10-03 11:12:04.307782
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ccb417c53a87"
down_revision: str | None = "041902abed20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    # pgvector ships in the pgvector/pgvector image; this enables it per DB.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    """Revert this revision."""
    op.execute("DROP EXTENSION IF EXISTS vector")
