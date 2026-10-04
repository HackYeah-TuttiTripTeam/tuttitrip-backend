"""Place address: printed on the plan next to the name.

Also merges the two heads on develop (mcp grant and interview sessions).

Revision ID: b81e5c2a7d14
Revises: 28a40fb2f40e
Create Date: 2026-10-04 03:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b81e5c2a7d14"
down_revision: str | Sequence[str] | None = "28a40fb2f40e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.add_column("places", sa.Column("address", sa.String(length=300), nullable=True))


def downgrade() -> None:
    """Revert this revision."""
    op.drop_column("places", "address")
