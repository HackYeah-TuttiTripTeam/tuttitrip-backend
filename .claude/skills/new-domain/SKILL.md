---
name: new-domain
description: Scaffold a new feature domain or subdomain in tuttitrip-backend (api.py, schemas.py, services/, optional models.py+db.py and logic/), register its router and add tests. Use when adding a new area of the product such as "add a budget domain" or "add a replanning subdomain under planning".
---

# New domain or subdomain

Layout and rules are in AGENTS.md ("Layout: vertical slices"). The architecture
tests in `tests/architecture/` will fail if you get them wrong.

1. Pick the place:
   - top-level domain: `src/tuttitrip/<name>/`
   - subdomain: `src/tuttitrip/<parent>/<name>/` (only if it has its own API,
     schemas and logic; otherwise extend the parent)
2. Create the files (snake_case `<name>`):
   - `__init__.py`: one-line docstring, **no imports**.
   - `schemas.py`: Pydantic DTOs only (no fastapi/sqlalchemy/pydantic_ai).
   - `services/__init__.py` (docstring) + at least one `services/<name>_service.py`.
   - `api.py`: `router = APIRouter(prefix="/<path>", tags=["<name>"])`;
     endpoints call services; map domain exceptions to `HTTPException`.
     Every endpoint gets `dependencies=[requires(Feature.X, Access.READ|WRITE)]`
     (or `public()`, which needs an entry in the test's allow-list).
   - Persists data? `models.py` (models on `tuttitrip.shared.db.base.Base`,
     cross-domain FKs as strings like `ForeignKey("trips.id")`) **and** `db.py`
     (queries for these tables only). Then run the `new-migration` skill.
   - Pure logic (solver, rules, math)? `logic/__init__.py` + `logic/<x>.py`;
     no I/O, no frameworks, no settings, no services.
3. Use other domains only through `tuttitrip.<other>.services...` or
   `tuttitrip.<other>.schemas`. Auth: `from tuttitrip.shared.auth.api import CurrentUser`;
   DB session: `from tuttitrip.shared.db.api import SessionDep`.
4. Register the router: import it in `src/tuttitrip/main.py` and add it to `ROUTERS`.
5. Register the feature nodes with the `new-permission` skill: a group
   `Feature.<NAME>` plus leaves (`<name>.core` for the domain's own data).
   Routes under `/trips/{trip_id}/...` also depend on `TripMember` /
   `TripCoHost` / `TripHost` from `tuttitrip.trips.api`.
6. Tests: `tests/domains/test_<name>.py` (logic unit tests; API via
   `TestClient(create_app())`; agents via `agent.override(model=TestModel(...))`).
7. Google docstrings with `Args:`/`Returns:` everywhere public.
8. Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest`.
