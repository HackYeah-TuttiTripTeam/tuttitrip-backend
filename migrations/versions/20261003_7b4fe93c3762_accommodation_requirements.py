"""Accommodation requirements, their version and the user grant.

The UNIQUE (trip_id, kind, key) index serves trip_id lookups.

Revision ID: 7b4fe93c3762
Revises: a42b7c1d9e03
Create Date: 2026-10-03 21:04:02.260252
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b4fe93c3762"
down_revision: str | None = "a42b7c1d9e03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "accommodation_requirements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("hard", sa.Boolean(), nullable=False),
        sa.Column(
            "nights",
            sa.ARRAY(sa.SmallInteger()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("max_distance_m", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "(kind = 'distance') = (max_distance_m IS NOT NULL)",
            name=op.f("ck_accommodation_requirements_distance"),
        ),
        sa.CheckConstraint(
            "kind IN ('amenity', 'platform', 'distance')",
            name=op.f("ck_accommodation_requirements_kind"),
        ),
        sa.CheckConstraint(
            "0 < ALL(nights)",
            name=op.f("ck_accommodation_requirements_nights_positive"),
        ),
        sa.CheckConstraint(
            "max_distance_m > 0",
            name=op.f("ck_accommodation_requirements_max_distance_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_accommodation_requirements_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accommodation_requirements")),
        sa.UniqueConstraint(
            "trip_id", "kind", "key", name=op.f("uq_accommodation_requirements_trip_id")
        ),
    )
    op.create_table(
        "accommodation_requirements_versions",
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_accommodation_requirements_versions_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "trip_id", name=op.f("pk_accommodation_requirements_versions")
        ),
    )
    op.execute(
        "INSERT INTO role_grants (role_name, feature, level) "
        "VALUES ('user', 'accommodation', 'WRITE') ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    """Revert this revision."""
    op.execute(
        "DELETE FROM role_grants WHERE role_name = 'user' AND feature = 'accommodation'"
    )
    op.drop_table("accommodation_requirements_versions")
    op.drop_table("accommodation_requirements")
