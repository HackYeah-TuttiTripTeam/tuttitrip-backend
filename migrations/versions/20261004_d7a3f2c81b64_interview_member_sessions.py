"""Interview sessions of a trip member, apart from the host's.

A session with ``profile_id`` is the member's own interview about their
interests; the trip's interview (host and co-hosts) keeps
``profile_id`` empty. One open session per trip for the host side, one per
profile for a member.

Revision ID: d7a3f2c81b64
Revises: b7c3e1f09a42
Create Date: 2026-10-04 15:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a3f2c81b64"
down_revision: str | None = "b7c3e1f09a42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPEN = "status = 'open'"


def upgrade() -> None:
    """Apply this revision."""
    op.add_column(
        "interview_sessions", sa.Column("profile_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_interview_sessions_profile_id_profiles"),
        "interview_sessions",
        "profiles",
        ["profile_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index(
        "uq_interview_sessions_open_trip",
        table_name="interview_sessions",
        postgresql_where=sa.text(OPEN),
    )
    op.create_index(
        "uq_interview_sessions_open_trip",
        "interview_sessions",
        ["trip_id"],
        unique=True,
        postgresql_where=sa.text(f"{OPEN} AND profile_id IS NULL"),
    )
    op.create_index(
        "uq_interview_sessions_open_profile",
        "interview_sessions",
        ["profile_id"],
        unique=True,
        postgresql_where=sa.text(f"{OPEN} AND profile_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.execute("DELETE FROM interview_sessions WHERE profile_id IS NOT NULL")
    op.drop_index(
        "uq_interview_sessions_open_profile",
        table_name="interview_sessions",
        postgresql_where=sa.text(f"{OPEN} AND profile_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_interview_sessions_open_trip",
        table_name="interview_sessions",
        postgresql_where=sa.text(f"{OPEN} AND profile_id IS NULL"),
    )
    op.create_index(
        "uq_interview_sessions_open_trip",
        "interview_sessions",
        ["trip_id"],
        unique=True,
        postgresql_where=sa.text(OPEN),
    )
    op.drop_constraint(
        op.f("fk_interview_sessions_profile_id_profiles"),
        "interview_sessions",
        type_="foreignkey",
    )
    op.drop_column("interview_sessions", "profile_id")
