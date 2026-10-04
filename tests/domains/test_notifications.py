"""Notifications: producer service, schemas, permission node, migration, grants."""

import asyncio
import uuid
from io import StringIO
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import Table

from tuttitrip.main import create_app as _create_app  # ruff: ignore[unused-import] - registers erasers
from tuttitrip.notifications.models import Notification
from tuttitrip.notifications.schemas import (
    NotificationAction,
    NotificationActionCode,
    NotificationRead,
    NotificationType,
)
from tuttitrip.notifications.services import notification_service
from tuttitrip.shared.admin_users.services import erasure
from tuttitrip.shared.permissions.registry import DESCRIPTIONS, Feature, is_leaf

REVISION = "b7c3e1f09a42"


def _parent_revision() -> str:
    """The migration's own parent, so the test follows a repointed ``down_revision``."""
    script = ScriptDirectory.from_config(Config(toml_file="pyproject.toml"))
    revision = script.get_revision(REVISION)
    assert revision is not None
    parent = revision.down_revision
    assert isinstance(parent, str)
    return parent


PARENT = _parent_revision()
TRIP = uuid.uuid4()


def _notify(**overrides: Any) -> list[dict[str, Any]]:  # ruff: ignore[any-type]
    """Run `notify` with a faked insert and return the rows it passed."""
    kwargs: dict[str, Any] = {
        "recipients": ["a", "b"],
        "type": NotificationType.MEMBER_JOINED,
        "trip_id": TRIP,
        "params": {"member_name": "Ola"},
        "actions": [NotificationAction(code=NotificationActionCode.OPEN_PEOPLE)],
        "dedupe_key": "k",
    }
    insert = AsyncMock(side_effect=lambda _s, rows: len(rows))
    with patch.object(notification_service.db, "insert_for_recipients", insert):
        asyncio.run(notification_service.notify(MagicMock(), **{**kwargs, **overrides}))
    call = insert.await_args
    assert call is not None
    return call.args[1]


def test_notify_builds_one_row_per_recipient_without_content() -> None:
    rows = _notify()
    assert [r["user_sub"] for r in rows] == ["a", "b"]
    assert rows[0] == {
        "user_sub": "a",
        "type": "member_joined",
        "trip_id": TRIP,
        "params": {"member_name": "Ola"},
        "actions": [{"code": "open_people", "params": {}}],
        "dedupe_key": "k",
    }


def test_notify_skips_the_actor_and_repeated_recipients() -> None:
    rows = _notify(recipients=["a", "b", "a", "c"], actor="b")
    assert [r["user_sub"] for r in rows] == ["a", "c"]


def test_notify_with_only_the_actor_creates_nothing() -> None:
    assert _notify(recipients=["a"], actor="a") == []


def test_resolve_marks_by_key() -> None:
    mark = AsyncMock(return_value=2)
    with patch.object(notification_service.db, "mark_read_by_key", mark):
        assert asyncio.run(notification_service.resolve(MagicMock(), "k")) == 2
    mark.assert_awaited_once()
    call = mark.await_args
    assert call is not None
    assert call.args[1] == "k"


def test_action_codes_are_a_closed_set() -> None:
    assert {c.value for c in NotificationActionCode} == {
        "open_trip",
        "open_people",
        "open_plan",
        "approve_proposal",
        "reject_proposal",
        "approve_budget",
        "reject_budget",
    }
    with pytest.raises(ValidationError):
        NotificationAction.model_validate({"code": "https://evil.example"})


def test_read_schema_exposes_no_content_and_no_user() -> None:
    assert set(NotificationRead.model_fields) == {
        "id",
        "type",
        "trip_id",
        "params",
        "actions",
        "read_at",
        "created_at",
    }


def test_type_column_has_no_check_so_new_types_need_no_migration() -> None:
    table = cast("Table", Notification.__table__)
    assert not [
        c for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
    ]
    assert {i.name for i in table.indexes} == {
        "ix_notifications_user_created",
        "ix_notifications_user_unread",
        "ix_notifications_dedupe_key",
        "ix_notifications_trip_id",
        "ix_notifications_created_at",
    }


def test_feature_is_a_described_leaf() -> None:
    assert is_leaf(Feature.NOTIFICATIONS)
    assert DESCRIPTIONS[Feature.NOTIFICATIONS]


def _sql(direction: str, revisions: str) -> str:
    out = StringIO()
    config = Config(toml_file="pyproject.toml", output_buffer=out)
    getattr(command, direction)(config, revisions, sql=True)
    return out.getvalue()


def test_migration_creates_table_trigger_and_user_grant_and_reverts_them() -> None:
    up = _sql("upgrade", f"{PARENT}:{REVISION}")
    down = _sql("downgrade", f"{REVISION}:{PARENT}")
    assert "CREATE TABLE notifications" in up
    assert "pg_notify" in up
    assert "AFTER INSERT ON notifications" in up
    assert "WHERE read_at IS NULL" in up
    assert "'user', 'notifications', 'WRITE'" in up
    assert "DROP TRIGGER notifications_notify" in down
    assert "DROP TABLE notifications" in down
    assert "feature = 'notifications'" in down


def test_erasing_an_account_deletes_its_notifications() -> None:
    delete = AsyncMock(return_value=4)
    with patch.object(notification_service.db, "delete_for_user", delete):
        counts = asyncio.run(notification_service.erase_account(MagicMock(), "sub"))
    assert counts == {"notifications_deleted": 4}
    assert delete.await_args is not None
    assert delete.await_args.args[1] == "sub"


def test_the_eraser_is_registered() -> None:
    assert notification_service.erase_account in erasure._ERASERS  # ruff: ignore[private-member-access]
