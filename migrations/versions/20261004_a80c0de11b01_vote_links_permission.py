"""Grant trips.vote_links to the user role.

Revision ID: a80c0de11b01
Revises: 9c1f5a7d3b20
Create Date: 2026-10-04 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a80c0de11b01"
down_revision: str | None = "9c1f5a7d3b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.execute(
        "INSERT INTO role_grants (role_name, feature, level) "
        "VALUES ('user', 'trips.vote_links', 'WRITE') ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    """Revert this revision."""
    op.execute(
        "DELETE FROM role_grants WHERE role_name = 'user' "
        "AND feature = 'trips.vote_links'"
    )
