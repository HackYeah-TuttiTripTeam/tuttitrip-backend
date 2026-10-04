"""Interview sessions and assistant values.

Revision ID: a7bad57ce3b3
Revises: 243e9787efa9
Create Date: 2026-10-04 01:50:58.543768
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7bad57ce3b3"
down_revision: str | None = "243e9787efa9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open", "closed", name="interview_status", native_enum=False, length=10
            ),
            server_default="open",
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "history",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_interview_sessions_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_interview_sessions")),
    )
    op.create_index(
        op.f("ix_interview_sessions_trip_id"),
        "interview_sessions",
        ["trip_id"],
        unique=False,
    )
    op.create_index(
        "uq_interview_sessions_open_trip",
        "interview_sessions",
        ["trip_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "interview_assistant_values",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column(
            "field",
            sa.Enum(
                "destination",
                "dates",
                "budget",
                "people",
                "preferences",
                name="knowledge_field",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("profile_id", sa.Uuid(), nullable=True),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name=op.f("fk_interview_assistant_values_profile_id_profiles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_interview_assistant_values_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_interview_assistant_values")),
    )
    op.create_index(
        "uq_interview_assistant_values_ref",
        "interview_assistant_values",
        ["trip_id", "field", "profile_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_index(
        "uq_interview_assistant_values_ref",
        table_name="interview_assistant_values",
        postgresql_nulls_not_distinct=True,
    )
    op.drop_table("interview_assistant_values")
    op.drop_index(
        "uq_interview_sessions_open_trip",
        table_name="interview_sessions",
        postgresql_where=sa.text("status = 'open'"),
    )
    op.drop_index(
        op.f("ix_interview_sessions_trip_id"), table_name="interview_sessions"
    )
    op.drop_table("interview_sessions")
