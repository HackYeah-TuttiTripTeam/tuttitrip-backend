"""Notifications: table, pg_notify trigger and the default user grant.

Revision ID: b7c3e1f09a42
Revises: 9c1f5a7d3b20, a7bad57ce3b3
Create Date: 2026-10-04 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7c3e1f09a42"
down_revision: str | Sequence[str] | None = ("9c1f5a7d3b20", "a7bad57ce3b3")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_sub", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=True),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_notifications_trip_id_trips"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("user_sub", "dedupe_key", name="uq_notifications_dedupe"),
    )
    op.create_index(
        "ix_notifications_user_created",
        "notifications",
        ["user_sub", sa.literal_column("created_at DESC")],
    )
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_sub"],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index(
        "ix_notifications_dedupe_key",
        "notifications",
        ["dedupe_key"],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index(
        "ix_notifications_trip_id",
        "notifications",
        ["trip_id"],
        postgresql_where=sa.text("trip_id IS NOT NULL"),
    )
    # NOTIFY is delivered after the commit, in commit order, and its payload is
    # capped at 8000 bytes, so only ids travel; the stream reads the row itself.
    op.execute(
        """
        CREATE FUNCTION notify_notification() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_notify(
                'notifications',
                json_build_object('id', NEW.id, 'user_sub', NEW.user_sub)::text
            );
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER notifications_notify AFTER INSERT ON notifications "
        "FOR EACH ROW EXECUTE FUNCTION notify_notification()"
    )
    op.execute(
        "INSERT INTO role_grants (role_name, feature, level) "
        "VALUES ('user', 'notifications', 'WRITE') ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    """Revert this revision."""
    op.execute(
        "DELETE FROM role_grants WHERE role_name = 'user' AND feature = 'notifications'"
    )
    op.execute("DROP TRIGGER notifications_notify ON notifications")
    op.execute("DROP FUNCTION notify_notification()")
    op.drop_index("ix_notifications_trip_id", table_name="notifications")
    op.drop_index("ix_notifications_dedupe_key", table_name="notifications")
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
