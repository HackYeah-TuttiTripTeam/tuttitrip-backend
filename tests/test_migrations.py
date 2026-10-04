"""Alembic keeps a single head: parallel migrations must be rebased or merged."""

from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

REVISION = '''"""Test revision."""

revision = "{rev}"
down_revision = {down}
'''


def test_alembic_has_one_head() -> None:
    config = Config(toml_file="pyproject.toml")
    assert len(ScriptDirectory.from_config(config).get_heads()) == 1


def test_two_revisions_with_the_same_parent_make_two_heads(tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    versions.mkdir()
    for rev, down in (("a", None), ("b", "'a'"), ("c", "'a'")):
        (versions / f"{rev}.py").write_text(REVISION.format(rev=rev, down=down))
    config = Config()
    config.set_main_option("script_location", str(tmp_path))
    assert len(ScriptDirectory.from_config(config).get_heads()) == 2


def _sql(direction: str, revisions: str) -> str:
    out = StringIO()
    config = Config(toml_file="pyproject.toml", output_buffer=out)
    getattr(command, direction)(config, revisions, sql=True)
    return out.getvalue()


def test_trip_details_migration_names_its_checks_once_both_ways() -> None:
    up = _sql("upgrade", "6801abbf6bbb:a33d0c7e5b21")
    down = _sql("downgrade", "a33d0c7e5b21:6801abbf6bbb")
    for name in ("dates", "day_window", "budget_total", "budget_day"):
        assert f"ADD CONSTRAINT ck_trips_{name} CHECK" in up
        assert f"DROP CONSTRAINT ck_trips_{name};" in down
    assert "ck_trips_ck_" not in up + down


def test_feedback_migration_grants_and_removes_the_user_role_permission() -> None:
    up = _sql("upgrade", "7b4fe93c3762:f3ed8c494ca7")
    down = _sql("downgrade", "f3ed8c494ca7:7b4fe93c3762")
    assert "VALUES ('user', 'profiles.feedback', 'WRITE') ON CONFLICT DO NOTHING" in up
    assert "role_name = 'user' AND feature = 'profiles.feedback'" in down


def test_sheet_rules_migration_adds_reduced_and_protects_sheet_rows_both_ways() -> None:
    up = _sql("upgrade", "9c1f5a7d3b20:28a40fb2f40e")
    down = _sql("downgrade", "28a40fb2f40e:9c1f5a7d3b20")
    assert "'family', 'reduced')" in up
    assert "current_user = 'tuttitrip_worker' AND OLD.source = 'sheet'" in up
    for trigger in ("places_keep_sheet_rows", "place_prices_keep_sheet_rows"):
        assert f"CREATE TRIGGER {trigger}" in up
        assert f"DROP TRIGGER {trigger}" in down
    assert "'student', 'family')" in down
    assert "ck_place_prices_ck_" not in up + down
