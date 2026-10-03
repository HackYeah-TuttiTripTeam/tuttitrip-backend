"""Grant planning.plans WRITE to the default role user.

Revision ID: a42b7c1d9e03
Revises: a33d0c7e5b21
Create Date: 2026-10-03 14:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a42b7c1d9e03"
down_revision: str | None = "a33d0c7e5b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.execute(
        "INSERT INTO role_grants (role_name, feature, level) "
        "VALUES ('user', 'planning.plans', 'WRITE') ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    """Revert this revision."""
    op.execute(
        "DELETE FROM role_grants "
        "WHERE role_name = 'user' AND feature = 'planning.plans'"
    )
