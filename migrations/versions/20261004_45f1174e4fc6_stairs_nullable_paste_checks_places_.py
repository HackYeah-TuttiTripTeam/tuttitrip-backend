"""Unknown stairs are NULL, paste checks and the places.candidates grant.

Revision ID: 45f1174e4fc6
Revises: d217a5e9c1b3
Create Date: 2026-10-04 04:53:09.413700
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "45f1174e4fc6"
down_revision: str | None = "d217a5e9c1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "paste_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.String(length=300), nullable=False),
        sa.Column("parsed", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "picks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column(
            "job_failed", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["id"],
            ["pasted_documents.id"],
            name=op.f("fk_paste_checks_id_pasted_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_paste_checks_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paste_checks")),
    )
    op.create_index(
        op.f("ix_paste_checks_trip_id"), "paste_checks", ["trip_id"], unique=False
    )
    # Unknown stairs are NULL, not 0 ("no stairs"). Rows of the OSM import never
    # knew them; sheet rows keep what they have until the sheet is imported again
    # (the importer writes NULL for an empty `schody` cell).
    op.alter_column(
        "places",
        "stairs",
        existing_type=sa.DOUBLE_PRECISION(precision=53),
        nullable=True,
        server_default=None,
    )
    op.execute("UPDATE places SET stairs = NULL WHERE source = 'osm'")
    op.execute(
        "INSERT INTO role_grants (role_name, feature, level) "
        "VALUES ('user', 'places.candidates', 'WRITE') ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    """Revert this revision."""
    op.execute(
        "DELETE FROM role_grants "
        "WHERE role_name = 'user' AND feature = 'places.candidates'"
    )
    op.execute("UPDATE places SET stairs = 0 WHERE stairs IS NULL")
    op.alter_column(
        "places",
        "stairs",
        existing_type=sa.DOUBLE_PRECISION(precision=53),
        nullable=False,
        server_default=sa.text("0"),
    )
    op.drop_index(op.f("ix_paste_checks_trip_id"), table_name="paste_checks")
    op.drop_table("paste_checks")
