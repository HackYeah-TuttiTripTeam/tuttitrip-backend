"""Google exports (Calendar, Drive), "anyway" suggestion state and the place marker.

backend#97, #98 and #100 in one revision: ``google_exports`` (what a user saved to
Google), ``anyway_states`` (host rejections, the model's text) and
``places.unique_experience``.

Revision ID: ab1dd832af00
Revises: 093e32d28baf
Create Date: 2026-10-04 06:13:10.773881
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ab1dd832af00"
down_revision: str | None = "093e32d28baf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "anyway_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("place_id", sa.Uuid(), nullable=False),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by_sub", sa.String(length=255), nullable=True),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["places.id"],
            name=op.f("fk_anyway_states_place_id_places"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plan_versions.id"],
            name=op.f("fk_anyway_states_plan_id_plan_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_anyway_states_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anyway_states")),
        sa.UniqueConstraint(
            "plan_id", "day", "place_id", name=op.f("uq_anyway_states_plan_id")
        ),
    )
    op.create_index(
        "ix_anyway_states_rejected",
        "anyway_states",
        ["trip_id"],
        unique=False,
        postgresql_where=sa.text("rejected_at IS NOT NULL"),
    )
    op.create_table(
        "google_exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("user_sub", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("web_link", sa.String(), nullable=True),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column("event_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('calendar', 'drive')", name=op.f("ck_google_exports_kind")
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plan_versions.id"],
            name=op.f("fk_google_exports_plan_id_plan_versions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_google_exports_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_google_exports")),
        sa.UniqueConstraint(
            "trip_id", "user_sub", "kind", name=op.f("uq_google_exports_trip_id")
        ),
    )
    op.add_column(
        "places",
        sa.Column(
            "unique_experience",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_column("places", "unique_experience")
    op.drop_table("google_exports")
    op.drop_index(
        "ix_anyway_states_rejected",
        table_name="anyway_states",
        postgresql_where=sa.text("rejected_at IS NOT NULL"),
    )
    op.drop_table("anyway_states")
