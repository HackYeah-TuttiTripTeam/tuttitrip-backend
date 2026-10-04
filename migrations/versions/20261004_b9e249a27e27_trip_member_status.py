"""Trip member participation status (pending, confirmed).

Revision ID: b9e249a27e27
Revises: 477ae4f2a2d8
Create Date: 2026-10-04 02:10:59.884386
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9e249a27e27"
down_revision: str | None = "477ae4f2a2d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.add_column(
        "trip_members",
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "confirmed",
                name="member_status",
                native_enum=False,
                length=10,
            ),
            server_default="pending",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_trip_members_status"),
        "trip_members",
        "status IN ('pending', 'confirmed')",
    )
    # A host is going by definition; everyone else confirms from now on.
    op.execute("UPDATE trip_members SET status = 'confirmed' WHERE role = 'host'")


def downgrade() -> None:
    """Revert this revision."""
    op.drop_constraint(op.f("ck_trip_members_status"), "trip_members", type_="check")
    op.drop_column("trip_members", "status")
