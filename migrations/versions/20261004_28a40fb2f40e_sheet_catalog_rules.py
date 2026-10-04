"""Sheet catalog rules: reduced ticket category, sheet rows closed to the worker.

Revision ID: 28a40fb2f40e
Revises: a7bad57ce3b3
Create Date: 2026-10-04 02:13:30.781618
"""

from collections.abc import Sequence

from alembic import op

revision: str = "28a40fb2f40e"
down_revision: str | None = "a7bad57ce3b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_place_prices_ticket_category"
_CATEGORIES = "'adult', 'child', 'senior', 'student', 'family'"
# The OSM import (tuttitrip-worker) connects as the role `tuttitrip_worker`. The
# hand-checked sheet rows are the source of truth: for rows with source = 'sheet'
# its writes are skipped (the statement succeeds, the row stays as it was).


def upgrade() -> None:
    """Apply this revision."""
    op.drop_constraint(op.f(_CONSTRAINT), "place_prices", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT),
        "place_prices",
        f"ticket_category IN ({_CATEGORIES}, 'reduced')",
    )
    op.execute(
        """
        CREATE FUNCTION places_keep_sheet_rows() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF current_user = 'tuttitrip_worker' AND OLD.source = 'sheet' THEN
                RETURN NULL;
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER places_keep_sheet_rows BEFORE UPDATE ON places
        FOR EACH ROW EXECUTE FUNCTION places_keep_sheet_rows()
        """
    )
    op.execute(
        """
        CREATE FUNCTION place_prices_keep_sheet_rows() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF current_user = 'tuttitrip_worker' AND EXISTS (
                SELECT 1 FROM places
                WHERE id = NEW.place_id AND source = 'sheet'
            ) THEN
                RETURN NULL;
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER place_prices_keep_sheet_rows
        BEFORE INSERT OR UPDATE ON place_prices
        FOR EACH ROW EXECUTE FUNCTION place_prices_keep_sheet_rows()
        """
    )


def downgrade() -> None:
    """Revert this revision."""
    op.execute("DROP TRIGGER place_prices_keep_sheet_rows ON place_prices")
    op.execute("DROP FUNCTION place_prices_keep_sheet_rows()")
    op.execute("DROP TRIGGER places_keep_sheet_rows ON places")
    op.execute("DROP FUNCTION places_keep_sheet_rows()")
    op.execute("DELETE FROM place_prices WHERE ticket_category = 'reduced'")
    op.drop_constraint(op.f(_CONSTRAINT), "place_prices", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT), "place_prices", f"ticket_category IN ({_CATEGORIES})"
    )
