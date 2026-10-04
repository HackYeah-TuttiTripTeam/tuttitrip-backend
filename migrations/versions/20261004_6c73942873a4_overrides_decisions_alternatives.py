"""Overrides decisions and plan alternatives.

Revision ID: 6c73942873a4
Revises: c4d9e1f07a52
Create Date: 2026-10-04 04:01:04.338916
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6c73942873a4"
down_revision: str | None = "c4d9e1f07a52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "plan_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("place_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("effects", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_sub", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('must', 'block', 'revoke', 'budget_approval')",
            name=op.f("ck_plan_decisions_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_plan_decisions_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_decisions")),
    )
    op.create_index(
        op.f("ix_plan_decisions_trip_id"), "plan_decisions", ["trip_id"], unique=False
    )
    op.create_table(
        "trip_overrides",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("place_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by_sub", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_sub", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "kind IN ('must', 'block')", name=op.f("ck_trip_overrides_kind")
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["places.id"],
            name=op.f("fk_trip_overrides_place_id_places"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_trip_overrides_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trip_overrides")),
    )
    op.create_index(
        op.f("ix_trip_overrides_trip_id"), "trip_overrides", ["trip_id"], unique=False
    )
    op.create_index(
        "uq_trip_overrides_active",
        "trip_overrides",
        ["trip_id", "place_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION plan_decisions_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            -- The cascade of deleting a trip runs one level deeper than a
            -- direct DELETE; only that one is allowed.
            IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'plan_decisions is append-only';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER plan_decisions_append_only
        BEFORE UPDATE OR DELETE ON plan_decisions
        FOR EACH ROW EXECUTE FUNCTION plan_decisions_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER plan_decisions_no_truncate
        BEFORE TRUNCATE ON plan_decisions
        FOR EACH STATEMENT EXECUTE FUNCTION plan_decisions_append_only()
        """
    )
    op.add_column(
        "plan_versions", sa.Column("alternative_of", sa.Uuid(), nullable=True)
    )
    op.drop_constraint(
        op.f("uq_plan_versions_trip_version"), "plan_versions", type_="unique"
    )
    op.create_index(
        "uq_plan_versions_trip_version",
        "plan_versions",
        ["trip_id", "version"],
        unique=True,
        postgresql_where=sa.text("alternative_of IS NULL"),
    )
    op.create_foreign_key(
        op.f("fk_plan_versions_alternative_of_plan_versions"),
        "plan_versions",
        "plan_versions",
        ["alternative_of"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_constraint(
        op.f("fk_plan_versions_alternative_of_plan_versions"),
        "plan_versions",
        type_="foreignkey",
    )
    op.drop_index(
        "uq_plan_versions_trip_version",
        table_name="plan_versions",
        postgresql_where=sa.text("alternative_of IS NULL"),
    )
    op.create_unique_constraint(
        op.f("uq_plan_versions_trip_version"),
        "plan_versions",
        ["trip_id", "version"],
        postgresql_nulls_not_distinct=False,
    )
    op.drop_column("plan_versions", "alternative_of")
    op.drop_index(
        "uq_trip_overrides_active",
        table_name="trip_overrides",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_index(op.f("ix_trip_overrides_trip_id"), table_name="trip_overrides")
    op.drop_table("trip_overrides")
    op.drop_index(op.f("ix_plan_decisions_trip_id"), table_name="plan_decisions")
    op.drop_table("plan_decisions")
    op.execute("DROP FUNCTION plan_decisions_append_only()")
