"""Merge heads after 190 and 170.

Revision ID: 6b826a460bb7
Revises: 0f8ab2d0e440, 65807aa11765
Create Date: 2026-10-04 04:23:38.982641
"""

from collections.abc import Sequence

revision: str = "6b826a460bb7"
down_revision: str | Sequence[str] | None = ("0f8ab2d0e440", "65807aa11765")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""


def downgrade() -> None:
    """Revert this revision."""
