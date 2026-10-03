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
    up = _sql("upgrade", "1c3eca9c16c6:a33d0c7e5b21")
    down = _sql("downgrade", "a33d0c7e5b21:1c3eca9c16c6")
    for name in ("dates", "day_window", "budget_total", "budget_day"):
        assert f"ADD CONSTRAINT ck_trips_{name} CHECK" in up
        assert f"DROP CONSTRAINT ck_trips_{name};" in down
    assert "ck_trips_ck_" not in up + down
