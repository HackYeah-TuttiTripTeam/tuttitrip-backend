"""Profile age and comfort; age_group becomes computed from the age.

Existing rows have no age, only a free-string ``age_group``. Each gets one
placeholder age inside its group (toddler is not in the old data) and the
comfort defaults of that group; unknown groups count as adult. The old column
is dropped: the group is derived from the age from now on.

Revision ID: 213f4a329ce3
Revises: a42b7c1d9e03
Create Date: 2026-10-03 20:20:25.091644
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "213f4a329ce3"
down_revision: str | None = "a42b7c1d9e03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Snapshot of the age defaults at the time of this revision, and the placeholder
# age given to old rows of that group.
# Columns: group, age, segment_km, daily_km, active_min, stairs, queue_min, nap,
# nap_min.
_CHILD = ("child", 8, 1.0, 4.0, 300, 0.6, 15, "13:00", 60)
_TEEN = ("teen", 15, 2.5, 9.0, 540, 0.1, 30, None, 0)
_ADULT = ("adult", 35, 3.0, 12.0, 600, 0.2, 40, None, 0)
_SENIOR = ("senior", 70, 1.5, 6.0, 420, 0.7, 20, "14:00", 30)
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
_UPDATE = (
    "UPDATE profiles SET age = :age, segment_km = :seg, daily_km = :day,"
    " active_min = :act, stairs_sensitivity = :stairs,"
    " queue_patience_min = :queue, nap_start = CAST(CAST(:nap AS text) AS time),"
    " nap_minutes = :nap_min, floor = 30 WHERE "
)


def upgrade() -> None:
    """Apply this revision."""
    op.add_column("profiles", sa.Column("user_sub", sa.String(length=255)))
    op.add_column("profiles", sa.Column("nap_start", sa.Time()))
    for name, type_ in _COLUMNS:
        op.add_column("profiles", sa.Column(name, type_))
    bind = op.get_bind()
    for group, *values in (_CHILD, _TEEN, _SENIOR):
        params = dict(zip(_KEYS, values, strict=True), grp=group)
        bind.execute(sa.text(_UPDATE + "age_group = :grp"), params)
    # Everything left (adult and any unknown string) is an adult.
    params = dict(zip(_KEYS, _ADULT[1:], strict=True))
    bind.execute(sa.text(_UPDATE + "age IS NULL"), params)
    for name, type_ in _COLUMNS:
        op.alter_column("profiles", name, existing_type=type_, nullable=False)
    op.drop_column("profiles", "age_group")
    op.create_unique_constraint(
        op.f("uq_profiles_trip_id"), "profiles", ["trip_id", "user_sub"]
    )
    op.create_check_constraint(
        op.f("ck_profiles_weight_positive"), "profiles", "weight > 0"
    )
    op.create_check_constraint(
        op.f("ck_profiles_floor_range"), "profiles", "floor BETWEEN 0 AND 100"
    )
    op.create_check_constraint(
        op.f("ck_profiles_stairs_sensitivity_range"),
        "profiles",
        "stairs_sensitivity BETWEEN 0 AND 1",
    )


def downgrade() -> None:
    """Revert this revision."""
    for name in ("stairs_sensitivity_range", "floor_range", "weight_positive"):
        op.drop_constraint(op.f(f"ck_profiles_{name}"), "profiles", type_="check")
    op.drop_constraint(op.f("uq_profiles_trip_id"), "profiles", type_="unique")
    op.add_column(
        "profiles",
        sa.Column(
            "age_group", sa.String(length=16), server_default="adult", nullable=False
        ),
    )
    op.execute(
        "UPDATE profiles SET age_group = CASE WHEN age < 13 THEN 'child'"
        " WHEN age < 18 THEN 'teen' WHEN age < 65 THEN 'adult' ELSE 'senior' END"
    )
    for name, _ in reversed(_COLUMNS):
        op.drop_column("profiles", name)
    op.drop_column("profiles", "nap_start")
    op.drop_column("profiles", "user_sub")
