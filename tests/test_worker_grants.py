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


def test_worker_has_no_access_to_permissions() -> None:
    sql = GRANTS.read_text()
    for table in (
        "roles",
        "role_grants",
        "user_roles",
        "user_grants",
        "permission_audit",
        "account_blocks",
        "trip_members",
    ):
        assert f"public.{table}" not in sql
    assert "ALL TABLES" not in sql.upper()
    assert "DEFAULT PRIVILEGES" not in sql.upper()


def _granted(privilege: str) -> set[str]:
    sql = "\n".join(
        line for line in GRANTS.read_text().splitlines() if not line.startswith("--")
    )
    found: set[str] = set()
    for match in re.finditer(rf"GRANT {privilege}\s+ON (.+?)\s+TO", sql, re.DOTALL):
        found |= set(re.findall(r"public\.([a-z_]+)", match.group(1)))
    return found


def test_worker_reads_pasted_texts_and_the_catalog() -> None:
    assert {
        "pasted_documents",
        "places",
        "cities",
        "place_prices",
        "transit_fares",
    } <= _granted("SELECT")


def test_worker_reads_plan_versions_for_justifications() -> None:
    assert "plan_versions" in _granted("SELECT")
    assert "plan_versions" not in _granted("SELECT, INSERT, UPDATE, DELETE")


def test_catalog_import_may_insert_but_never_delete() -> None:
    assert _granted("INSERT") == {"places", "cities", "place_prices"}
    assert not {"places", "cities", "pasted_documents"} & _granted(
        "SELECT, INSERT, UPDATE, DELETE"
    )


def test_osm_import_updates_only_the_columns_its_upsert_sets() -> None:
    sql = GRANTS.read_text()
    assert _granted("UPDATE") == {"place_prices"}  # table-wide; none on cities/places
    match = re.search(r"GRANT UPDATE \(([^)]+)\)\s+ON public\.places", sql)
    assert match is not None
    columns = {name.strip() for name in match.group(1).split(",")}
    assert columns == {
        "name", "category", "tags", "lat", "lon", "wheelchair", "indoor",
        "cuisine", "diet_tags", "amenities", "opening_hours",
    }  # fmt: skip
    assert not columns & {"city_slug", "source", "hours_verified", "osm_id", "id"}


def test_worker_writes_osm_fetch_state_without_delete() -> None:
    sql = "\n".join(
        line for line in GRANTS.read_text().splitlines() if not line.startswith("--")
    )
    match = re.search(r"GRANT SELECT, INSERT, UPDATE\s+ON (.+?)\s+TO", sql, re.DOTALL)
    assert match is not None
    assert set(re.findall(r"public\.([a-z_]+)", match.group(1))) == {
        "city_fetches",
        "city_fetch_attempts",
    }
