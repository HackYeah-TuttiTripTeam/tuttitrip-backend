"""Pin down pytest-archon behaviour the rules in this package rely on."""

import importlib
import sys
import textwrap
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_archon import archrule
from pytest_archon.failure import pop_failures


@pytest.fixture
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A throwaway package: a -> b -> fastapi, and c/__init__ -> fastapi."""
    name = f"archon_probe_{uuid.uuid4().hex}"
    root = tmp_path / name
    (root / "c").mkdir(parents=True)
    files = {
        "__init__.py": "",
        "a.py": f"from {name} import b\n",
        "b.py": "import fastapi\n",
        "c/__init__.py": "import fastapi\n",
        "c/d.py": "",
        "e.py": f"from {name}.c import d\n",
    }
    for rel, body in files.items():
        (root / rel).write_text(textwrap.dedent(body))
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    yield name
    sys.modules.pop(name, None)


def _violations(probe: str, module: str, *, direct: bool) -> list[str]:
    (
        archrule("probe")
        .match(f"{probe}.{module}")
        .should_not_import("fastapi*")
        .check(probe, only_direct_imports=direct)
    )
    return [f.reason for f in pop_failures()]


def test_should_not_import_is_transitive_by_default(probe: str) -> None:
    assert _violations(probe, "a", direct=False)


def test_only_direct_imports_ignores_indirect_chains(probe: str) -> None:
    assert _violations(probe, "a", direct=True) == []


def test_parent_package_init_is_not_followed(probe: str) -> None:
    # Importing `probe.c.d` runs `probe/c/__init__.py` at runtime, but archon
    # does not see it. That is why __init__.py files must stay import-free.
    assert _violations(probe, "e", direct=False) == []
