"""Pasted documents (plan and offer texts) and WRITE on planning.linter for users.

The text is deleted with the trip (ON DELETE CASCADE); the worker reads it by id.

Revision ID: 2647fc89c3ae
Revises: a42b7c1d9e03
Create Date: 2026-10-03 21:04:29.613225
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2647fc89c3ae"
down_revision: str | None = "a42b7c1d9e03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "pasted_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('plan', 'offer')", name=op.f("ck_pasted_documents_kind")
        ),
        sa.CheckConstraint(
            "char_length(text) BETWEEN 1 AND 20000",
            name=op.f("ck_pasted_documents_text_length"),
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_pasted_documents_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pasted_documents")),
    )
    op.create_index(
        op.f("ix_pasted_documents_trip_id"),
        "pasted_documents",
        ["trip_id"],
        unique=False,
    )
    op.execute(
        "INSERT INTO role_grants (role_name, feature, level) "
        "VALUES ('user', 'planning.linter', 'WRITE') "
        "ON CONFLICT (role_name, feature) DO UPDATE SET level = 'WRITE'"
    )


def downgrade() -> None:
    """Revert this revision."""
    op.execute(
        "UPDATE role_grants SET level = 'READ' "
        "WHERE role_name = 'user' AND feature = 'planning.linter'"
    )
    op.drop_index(op.f("ix_pasted_documents_trip_id"), table_name="pasted_documents")
    op.drop_table("pasted_documents")
