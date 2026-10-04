"""Accommodation offers.

Revision ID: 0f8ab2d0e440
Revises: 243e9787efa9
Create Date: 2026-10-04 00:59:35.183366
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0f8ab2d0e440"
down_revision: str | None = "243e9787efa9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "accommodation_offers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("nights", postgresql.ARRAY(sa.Date()), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("platform", sa.String(length=16), nullable=True),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("requirements_version", sa.Integer(), nullable=False),
        sa.Column(
            "requested_keys", postgresql.ARRAY(sa.String(length=64)), nullable=False
        ),
        sa.Column("job_id", sa.String(length=300), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "platform IN ('airbnb', 'booking')",
            name=op.f("ck_accommodation_offers_platform"),
        ),
        sa.CheckConstraint(
            "cardinality(nights) >= 1", name=op.f("ck_accommodation_offers_nights")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["pasted_documents.id"],
            name=op.f("fk_accommodation_offers_document_id_pasted_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_accommodation_offers_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accommodation_offers")),
    )
    op.create_index(
        op.f("ix_accommodation_offers_document_id"),
        "accommodation_offers",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_accommodation_offers_trip_id"),
        "accommodation_offers",
        ["trip_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_index(
        op.f("ix_accommodation_offers_trip_id"), table_name="accommodation_offers"
    )
    op.drop_index(
        op.f("ix_accommodation_offers_document_id"), table_name="accommodation_offers"
    )
    op.drop_table("accommodation_offers")
