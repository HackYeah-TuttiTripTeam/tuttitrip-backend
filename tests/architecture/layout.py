"""Discovery of domains and modules in the `tuttitrip` package.

Conventions (see AGENTS.md):

* A *feature domain* is a direct subpackage of `tuttitrip` other than `shared`.
* A *subdomain* is a subpackage of a domain that is not one of its layer
  packages (`services`, `logic`). Subdomains follow the same layout.
* Shared subdomains live in `tuttitrip/shared/<name>/`.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import tuttitrip

PACKAGE = "tuttitrip"
PACKAGE_ROOT = Path(tuttitrip.__file__).parent
SHARED = "shared"
LAYER_PACKAGES = frozenset({"services", "logic"})
DOMAIN_FILES = frozenset(
    {"__init__.py", "api.py", "schemas.py", "models.py", "db.py", "constants.py"}
)
REQUIRED_DOMAIN_FILES = ("__init__.py", "api.py", "schemas.py", "services/__init__.py")


def is_package(path: Path) -> bool:
    return path.is_dir() and (path / "__init__.py").is_file()


def subpackages(path: Path) -> list[Path]:
    return sorted(p for p in path.iterdir() if is_package(p))


def subdomains(domain: Path) -> list[Path]:
    return [p for p in subpackages(domain) if p.name not in LAYER_PACKAGES]


def walk_domains(domain: Path) -> Iterator[Path]:
    yield domain
    for sub in subdomains(domain):
        yield from walk_domains(sub)


def top_level_domains() -> list[str]:
    return [p.name for p in subpackages(PACKAGE_ROOT) if p.name != SHARED]


def feature_domains() -> list[Path]:
    """Every feature domain and subdomain, depth first."""
    return [
        d
        for top in subpackages(PACKAGE_ROOT)
        if top.name != SHARED
        for d in walk_domains(top)
    ]


def shared_subdomains() -> list[Path]:
    return subpackages(PACKAGE_ROOT / SHARED)


def module_name(path: Path) -> str:
    rel = path.relative_to(PACKAGE_ROOT.parent).with_suffix("")
    parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
    return ".".join(parts)


def all_modules() -> list[Path]:
    return sorted(p for p in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def imports_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append("." * node.level + (node.module or ""))
    return names


def rel(path: Path) -> str:
    return str(path.relative_to(PACKAGE_ROOT.parent))
