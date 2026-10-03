"""Profile age and comfort.

Revision ID: 213f4a329ce3
Revises: 1c3eca9c16c6
Create Date: 2026-10-03 20:20:25.091644
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "213f4a329ce3"
down_revision: str | None = "1c3eca9c16c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Snapshot of the age defaults at the time of this revision (existing rows get
# them from their age group; the age itself is a representative one).
# Columns: group, age, segment_km, daily_km, active_min, stairs, queue_min, nap,
# nap_min.
_BACKFILL = (
    ("child", 8, 1.0, 4.0, 300, 0.6, 15, "13:00", 60),
    ("teen", 15, 2.5, 9.0, 540, 0.1, 30, None, 0),
    ("adult", 35, 3.0, 12.0, 600, 0.2, 40, None, 0),
    ("senior", 70, 1.5, 6.0, 420, 0.7, 20, "14:00", 30),
)
_COLUMNS = (
    ("age", sa.Integer()),
    ("segment_km", sa.Double()),
    ("daily_km", sa.Double()),
    ("active_min", sa.Integer()),
    ("stairs_sensitivity", sa.Double()),
    ("queue_patience_min", sa.Integer()),
    ("nap_minutes", sa.Integer()),
    ("floor", sa.Integer()),
)


_KEYS = ("age", "seg", "day", "act", "stairs", "queue", "nap", "nap_min")


_SET = (
    "UPDATE profiles SET age = :age, segment_km = :seg, daily_km = :day,"
    " active_min = :act, stairs_sensitivity = :stairs,"
    " queue_patience_min = :queue, nap_start = CAST(CAST(:nap AS text) AS time),"
    " nap_minutes = :nap_min, floor = 30"
)
_BY_GROUP = sa.text(_SET + " WHERE age_group = :grp")
_UNKNOWN_GROUP = sa.text(_SET + " WHERE age IS NULL")


def _backfill(statement: sa.TextClause, row: tuple[object, ...]) -> None:
    params = dict(zip(_KEYS, row[1:], strict=True))
    if statement is _BY_GROUP:
        params["grp"] = row[0]
    op.get_bind().execute(statement, params)


def upgrade() -> None:
    """Apply this revision."""
    op.add_column("profiles", sa.Column("user_sub", sa.String(length=255)))
    op.add_column("profiles", sa.Column("nap_start", sa.Time()))
    for name, type_ in _COLUMNS:
        op.add_column("profiles", sa.Column(name, type_))
    for row in _BACKFILL:
        _backfill(_BY_GROUP, row)
    # age_group was a free string: anything unknown is treated as adult.
    _backfill(_UNKNOWN_GROUP, next(r for r in _BACKFILL if r[0] == "adult"))
    for name, type_ in _COLUMNS:
        op.alter_column("profiles", name, existing_type=type_, nullable=False)
    op.create_unique_constraint(
        op.f("uq_profiles_trip_id"), "profiles", ["trip_id", "user_sub"]
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_constraint(op.f("uq_profiles_trip_id"), "profiles", type_="unique")
    for name, _ in reversed(_COLUMNS):
        op.drop_column("profiles", name)
    op.drop_column("profiles", "nap_start")
    op.drop_column("profiles", "user_sub")
