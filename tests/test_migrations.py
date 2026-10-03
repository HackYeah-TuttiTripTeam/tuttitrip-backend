"""Alembic keeps a single head: parallel migrations must be rebased or merged."""

from pathlib import Path

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
