"""Structural rules: every domain slice has the same, predictable layout."""

import importlib
from pathlib import Path

import pytest

from tests.architecture.layout import (
    DOMAIN_FILES,
    LAYER_PACKAGES,
    PACKAGE_ROOT,
    REQUIRED_DOMAIN_FILES,
    all_modules,
    feature_domains,
    imports_in,
    is_package,
    module_name,
    rel,
    shared_subdomains,
    subdomains,
)
from tuttitrip.main import ROUTERS

DOMAINS = feature_domains()
SHARED_SUBDOMAINS = shared_subdomains()


def test_there_are_domains_to_check() -> None:
    assert DOMAINS
    assert SHARED_SUBDOMAINS


def test_top_level_package_holds_only_domains_and_the_app_factory() -> None:
    allowed_files = {"__init__.py", "main.py", "py.typed"}
    stray = [
        p.name
        for p in PACKAGE_ROOT.iterdir()
        if p.name != "__pycache__"
        and not (p.is_file() and p.name in allowed_files)
        and not is_package(p)
    ]
    assert stray == []


@pytest.mark.parametrize("domain", DOMAINS, ids=rel)
def test_domain_has_required_files(domain: Path) -> None:
    missing = [f for f in REQUIRED_DOMAIN_FILES if not (domain / f).is_file()]
    assert missing == []


@pytest.mark.parametrize("domain", [*DOMAINS, *SHARED_SUBDOMAINS], ids=rel)
def test_db_module_exists_iff_models_exist(domain: Path) -> None:
    assert (domain / "db.py").is_file() == (domain / "models.py").is_file()


@pytest.mark.parametrize("domain", DOMAINS, ids=rel)
def test_domain_contains_only_known_files(domain: Path) -> None:
    sub_names = {p.name for p in subdomains(domain)}
    stray = [
        p.name
        for p in domain.iterdir()
        if p.name != "__pycache__"
        and p.name not in DOMAIN_FILES
        and p.name not in LAYER_PACKAGES
        and p.name not in sub_names
    ]
    assert stray == [], "put new code in services/ or logic/, or make a subdomain"


@pytest.mark.parametrize("domain", [*DOMAINS, *SHARED_SUBDOMAINS], ids=rel)
def test_layer_packages_have_at_least_one_module(domain: Path) -> None:
    for layer in LAYER_PACKAGES:
        package = domain / layer
        if not package.exists():
            continue
        assert is_package(package), f"{rel(package)} needs an __init__.py"
        modules = [p for p in package.glob("*.py") if p.name != "__init__.py"]
        assert modules, f"{rel(package)} has no modules"


@pytest.mark.parametrize(
    "init", [p for p in all_modules() if p.name == "__init__.py"], ids=rel
)
def test_init_modules_import_nothing(init: Path) -> None:
    # pytest-archon reads `import` statements only; it does not model that
    # importing `a.b.c` executes `a/__init__.py` and `a/b/__init__.py`.
    # Import-free `__init__.py` files keep its import graph equal to runtime.
    assert imports_in(init) == []


# `shared/db/api.py` only exposes a dependency, so not every api.py has a router.
API_ROUTERS = {
    name: module.router
    for name in (module_name(p) for p in all_modules() if p.name == "api.py")
    if hasattr(module := importlib.import_module(name), "router")
}


@pytest.mark.parametrize("module", sorted(API_ROUTERS))
def test_every_router_is_registered_in_the_app(module: str) -> None:
    assert any(API_ROUTERS[module] is registered for registered in ROUTERS)
