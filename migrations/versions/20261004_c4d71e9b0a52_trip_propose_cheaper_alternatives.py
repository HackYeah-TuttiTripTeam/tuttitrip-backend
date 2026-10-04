"""Trip setting: propose cheaper alternatives after a day goes over budget.

Revision ID: c4d71e9b0a52
Revises: 6c73942873a4
Create Date: 2026-10-04 05:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d71e9b0a52"
down_revision: str | Sequence[str] | None = "6c73942873a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.add_column(
        "trips",
        sa.Column(
            "propose_cheaper_alternatives",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_column("trips", "propose_cheaper_alternatives")
