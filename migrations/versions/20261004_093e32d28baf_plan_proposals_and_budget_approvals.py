"""Plan proposals and budget approvals.

Revision ID: 093e32d28baf
Revises: 6c73942873a4
Create Date: 2026-10-04 04:45:59.492009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "093e32d28baf"
down_revision: str | None = "6c73942873a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "budget_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("flex_plan_id", sa.Uuid(), nullable=False),
        sa.Column("strict_plan_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("over_budget", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("kappa", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("gain_profile_id", sa.Uuid(), nullable=True),
        sa.Column("gain_points", sa.Double(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("active_plan_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_by_sub", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status IN ('approved', 'rejected')) = (decided_by_sub IS NOT NULL)",
            name=op.f("ck_budget_approvals_decided"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'superseded')",
            name=op.f("ck_budget_approvals_status"),
        ),
        sa.ForeignKeyConstraint(
            ["active_plan_id"],
            ["plan_versions.id"],
            name=op.f("fk_budget_approvals_active_plan_id_plan_versions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["flex_plan_id"],
            ["plan_versions.id"],
            name=op.f("fk_budget_approvals_flex_plan_id_plan_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["strict_plan_id"],
            ["plan_versions.id"],
            name=op.f("fk_budget_approvals_strict_plan_id_plan_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_budget_approvals_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_approvals")),
    )
    op.create_index(
        op.f("ix_budget_approvals_flex_plan_id"),
        "budget_approvals",
        ["flex_plan_id"],
        unique=False,
    )
    op.create_index(
        "ix_budget_approvals_trip_created_at",
        "budget_approvals",
        ["trip_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_budget_approvals_pending",
        "budget_approvals",
        ["trip_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.execute(
        """
        CREATE FUNCTION budget_approvals_decided_is_final() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            -- A decision is written once. Only active_plan_id may still change:
            -- deleting a plan version sets it to NULL.
            IF OLD.status <> 'pending' AND (
                NEW.id, NEW.trip_id, NEW.flex_plan_id, NEW.strict_plan_id,
                NEW.currency, NEW.over_budget, NEW.kappa, NEW.gain_profile_id,
                NEW.gain_points, NEW.status, NEW.created_at, NEW.decided_by_sub,
                NEW.decided_at
            ) IS DISTINCT FROM (
                OLD.id, OLD.trip_id, OLD.flex_plan_id, OLD.strict_plan_id,
                OLD.currency, OLD.over_budget, OLD.kappa, OLD.gain_profile_id,
                OLD.gain_points, OLD.status, OLD.created_at, OLD.decided_by_sub,
                OLD.decided_at
            ) THEN
                RAISE EXCEPTION 'a decided budget approval does not change';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER budget_approvals_decided_is_final
        BEFORE UPDATE ON budget_approvals
        FOR EACH ROW EXECUTE FUNCTION budget_approvals_decided_is_final()
        """
    )
    op.create_table(
        "plan_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("plan_hash", sa.String(length=12), nullable=False),
        sa.Column("sent_by", sa.String(length=255), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'superseded')", name=op.f("ck_plan_proposals_status")
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plan_versions.id"],
            name=op.f("fk_plan_proposals_plan_id_plan_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_plan_proposals_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_proposals")),
    )
    op.create_index(
        op.f("ix_plan_proposals_plan_id"), "plan_proposals", ["plan_id"], unique=False
    )
    op.create_index(
        "ix_plan_proposals_trip_sent_at",
        "plan_proposals",
        ["trip_id", "sent_at"],
        unique=False,
    )
    op.create_index(
        "uq_plan_proposals_open",
        "plan_proposals",
        ["trip_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "proposal_responses",
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("member_sub", sa.String(length=255), nullable=False),
        sa.Column("decision", sa.String(length=8), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column(
            "responded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject', 'comment')",
            name=op.f("ck_proposal_responses_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["plan_proposals.id"],
            name=op.f("fk_proposal_responses_proposal_id_plan_proposals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "proposal_id", "member_sub", name=op.f("pk_proposal_responses")
        ),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_table("proposal_responses")
    op.drop_index(
        "uq_plan_proposals_open",
        table_name="plan_proposals",
        postgresql_where=sa.text("status = 'open'"),
    )
    op.drop_index("ix_plan_proposals_trip_sent_at", table_name="plan_proposals")
    op.drop_index(op.f("ix_plan_proposals_plan_id"), table_name="plan_proposals")
    op.drop_table("plan_proposals")
    op.drop_index(
        "uq_budget_approvals_pending",
        table_name="budget_approvals",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index("ix_budget_approvals_trip_created_at", table_name="budget_approvals")
    op.drop_index(
        op.f("ix_budget_approvals_flex_plan_id"), table_name="budget_approvals"
    )
    op.drop_table("budget_approvals")
    op.execute("DROP FUNCTION budget_approvals_decided_is_final()")
