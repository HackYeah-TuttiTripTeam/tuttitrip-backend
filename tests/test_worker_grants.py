"""deploy/worker-grants.sql may only name tables that the models define."""

import importlib
import pkgutil
import re
from pathlib import Path

import tuttitrip
from tuttitrip.shared.db.base import Base

GRANTS = Path(__file__).resolve().parents[1] / "deploy" / "worker-grants.sql"


def granted_tables() -> set[str]:
    sql = "\n".join(
        line for line in GRANTS.read_text().splitlines() if not line.startswith("--")
    )
    return set(re.findall(r"public\.([a-z_][a-z0-9_]*)", sql))


def test_granted_tables_exist() -> None:
    for module in pkgutil.walk_packages(tuttitrip.__path__, prefix="tuttitrip."):
        if module.name.endswith(".models"):
            importlib.import_module(module.name)
    tables = granted_tables()
    assert tables
    assert tables <= set(Base.metadata.tables)


def test_worker_writes_only_its_tables() -> None:
    writable = re.search(
        r"INSERT, UPDATE, DELETE\s+ON (.+?)\s+TO", GRANTS.read_text(), re.DOTALL
    )
    assert writable is not None
    names = set(re.findall(r"public\.([a-z_]+)", writable.group(1)))
    assert names == {"worker_heartbeats", "job_results", "embeddings"}
