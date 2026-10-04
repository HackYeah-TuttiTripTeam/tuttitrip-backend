"""MCP access for the default user role.

Revision ID: 9c1f5a7d3b20
Revises: a7bad57ce3b3
Create Date: 2026-10-04 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "9c1f5a7d3b20"
down_revision: str | None = "a7bad57ce3b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.execute(
        "INSERT INTO role_grants (role_name, feature, level) "
        "VALUES ('user', 'mcp', 'READ') ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    """Revert this revision."""
    op.execute("DELETE FROM role_grants WHERE role_name = 'user' AND feature = 'mcp'")
