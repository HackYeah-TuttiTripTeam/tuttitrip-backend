"""Dependency rules between domains and layers (pytest-archon).

How pytest-archon resolves imports (checked in its source, v0.0.7):

* ``should_not_import`` is TRANSITIVE by default: it follows imports through
  every module of the checked package. ``only_direct_imports=True`` limits it
  to the module's own import statements.
* Imports inside functions and ``if TYPE_CHECKING:`` blocks are included.
* Parent ``__init__.py`` execution is NOT modelled; `test_structure` keeps all
  ``__init__.py`` files import-free so the graph matches runtime.
* A rule whose ``match`` selects no module fails ("NO CANDIDATES MATCHED").

Purity rules use transitive checks (a pure module must not reach a framework
through any chain). Layering rules ("who may import X directly") use direct
checks, because legitimate chains such as api -> services -> sqlalchemy would
otherwise be reported.
"""

import pytest
from pytest_archon import archrule

from tests.architecture.layout import PACKAGE, top_level_domains

FEATURE_DOMAINS = top_level_domains()
FRAMEWORKS = (
    "fastapi",
    "starlette",
    "pydantic_ai",
    "sqlalchemy",
    "asyncpg",
    "httpx",
    "dbos",
    "pgvector",
)


def _tree(name: str) -> tuple[str, str]:
    """Glob patterns for a package and everything below it."""
    return (name, f"{name}.*")


@pytest.mark.parametrize("domain", FEATURE_DOMAINS)
def test_shared_does_not_import_feature_domains(domain: str) -> None:
    (
        archrule(
            "shared kernel is independent", comment="shared must not know features"
        )
        .match(*_tree(f"{PACKAGE}.shared"))
        .should_not_import(*_tree(f"{PACKAGE}.{domain}"))
        .check(PACKAGE)
    )


@pytest.mark.parametrize("domain", FEATURE_DOMAINS)
def test_domains_talk_only_through_services_and_schemas(domain: str) -> None:
    others = [d for d in FEATURE_DOMAINS if d != domain]
    forbidden = [p for other in others for p in _tree(f"{PACKAGE}.{other}")]
    allowed = [
        pattern
        for other in others
        for pattern in (
            f"{PACKAGE}.{other}.schemas",
            f"{PACKAGE}.{other}.services",
            f"{PACKAGE}.{other}.services.*",
            f"{PACKAGE}.{other}.*.schemas",
            f"{PACKAGE}.{other}.*.services",
            f"{PACKAGE}.{other}.*.services.*",
            # HTTP dependencies such as `TripAccess` (only api modules may
            # import api modules, see the next test).
            f"{PACKAGE}.{other}.api",
            f"{PACKAGE}.{other}.*.api",
        )
    ]
    (
        archrule(
            "cross-domain access",
            comment="other domains: services/schemas (api: also api), never db/logic",
        )
        .match(*_tree(f"{PACKAGE}.{domain}"))
        .should_not_import(*forbidden)
        .may_import(*allowed)
        .check(PACKAGE, only_direct_imports=True)
    )


def test_only_api_modules_import_api_modules() -> None:
    # api -> api is allowed (e.g. `TripAccess` from trips.api); services, db,
    # models, logic and schemas never depend on the HTTP layer.
    (
        archrule("HTTP layer on top", use_regex=True)
        .match(rf"^{PACKAGE}(\.|$)")
        .exclude(r"\.api$", rf"^{PACKAGE}\.main$")
        .should_not_import(rf"^{PACKAGE}\..*\.api$")
        .check(PACKAGE, only_direct_imports=True)
    )


def test_only_api_modules_import_fastapi() -> None:
    (
        archrule("fastapi stays in api.py", use_regex=True)
        .match(rf"^{PACKAGE}(\.|$)")
        .exclude(r"\.api$", rf"^{PACKAGE}\.main$")
        .should_not_import(r"^fastapi(\.|$)", r"^starlette(\.|$)")
        .check(PACKAGE, only_direct_imports=True)
    )


def test_only_services_import_pydantic_ai() -> None:
    (
        archrule("agents live in services", use_regex=True)
        .match(rf"^{PACKAGE}(\.|$)")
        .exclude(r"\.services(\.|$)")
        .should_not_import(r"^pydantic_ai(\.|$)")
        .check(PACKAGE, only_direct_imports=True)
    )


def test_only_persistence_layers_import_sqlalchemy() -> None:
    (
        archrule("ORM stays in models/db/services and shared.db", use_regex=True)
        .match(rf"^{PACKAGE}(\.|$)")
        .exclude(
            r"\.(models|db)$", r"\.services(\.|$)", rf"^{PACKAGE}\.shared\.db(\.|$)"
        )
        .should_not_import(r"^sqlalchemy(\.|$)", r"^asyncpg(\.|$)", r"^pgvector(\.|$)")
        .check(PACKAGE, only_direct_imports=True)
    )


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_pure_modules_do_not_reach_frameworks(framework: str) -> None:
    # Pure = every `logic` package and every `schemas.py` (transitive check).
    (
        archrule("pure logic and DTOs are framework-free", use_regex=True)
        .match(r"\.logic(\.|$)", r"\.schemas$", r"\.constants$")
        .should_not_import(rf"^{framework}(\.|$)")
        .check(PACKAGE)
    )


def test_logic_does_not_reach_io_layers() -> None:
    (
        archrule("logic is pure", use_regex=True)
        .match(r"\.logic(\.|$)")
        .should_not_import(
            r"\.(api|db|models)$",
            r"\.services(\.|$)",
            rf"^{PACKAGE}\.shared\.(db|config|auth)(\.|$)",
            rf"^{PACKAGE}\.main$",
        )
        .check(PACKAGE)
    )


def test_only_shared_jobs_services_talk_to_dbos() -> None:
    (
        archrule("DBOS client stays in shared.jobs.services", use_regex=True)
        .match(rf"^{PACKAGE}(\.|$)")
        .exclude(rf"^{PACKAGE}\.shared\.jobs\.services(\.|$)")
        .should_not_import(r"^dbos(\.|$)")
        .check(PACKAGE, only_direct_imports=True)
    )
