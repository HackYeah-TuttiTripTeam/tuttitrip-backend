# AGENTS.md

Canonical instructions for anyone (human or agent) changing this repository.
`CLAUDE.md` imports this file. The user-facing docs are in `README.md` (Polish).

## What this is

TuttiTrip (working name WARTO) is the backend of a group/family trip planner
built at HackYeah 2026. FastAPI + Pydantic + Pydantic AI on Python 3.14, async
SQLAlchemy 2 on PostgreSQL, Auth0 for login. The web client consumes the
OpenAPI schema (`/openapi.json`) through a generated TypeScript client.

The product rule that shapes the code: deterministic logic (fairness solver,
plan linter, pricing, settlement) never depends on FastAPI, Pydantic AI or the
database. LLM agents only draft; pure code decides. Tests enforce this.

## Commands

```bash
uv sync                                   # install (incl. dev group)
cp .env.example .env                      # local settings
docker compose up -d db                   # Postgres only
uv run alembic upgrade head               # migrate
uv run uvicorn tuttitrip.main:app --reload
docker compose up --build                 # db + migrate + api in containers

# Must all pass before every commit (CI runs the same):
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
```

## Layout: vertical slices

```text
src/tuttitrip/
  main.py              composition root: create_app(), ROUTERS, lifespan
  shared/              shared kernel, imports no feature domain
    config/            Settings (pydantic-settings)
    db/                Base, async engine/sessions, SessionDep (db/api.py)
    auth/              Auth0 JWT verification, CurrentUser, GET /me
    health/            GET /health (DB + worker), GET /health/live
    jobs/              DBOS client: enqueue/status/cancel worker jobs, contract mirror
  trips/               reference slice: api -> services -> db -> models
  profiles/            people on a trip (weights, age groups)
  interview/           AI interview agent (AG-UI endpoint goes here)
  planning/            planner agent; subdomains fairness/ and linter/
  accommodation/       requirements contract (met/unmet/unconfirmed)
  expenses/            expenses; subdomain settlement/
  search/              pgvector embeddings (written by the worker)
contracts/             jobs.schema.json: rendered job contract (compared with the worker)
migrations/            Alembic (async); versions/ holds revisions
deploy/                host deployment scripts (bash), gateway config
tests/architecture/    structure + dependency rules (pytest-archon)
```

### Files in a domain or subdomain

| File / package | Required | Contents |
| --- | --- | --- |
| `__init__.py` | yes | Docstring only. **No imports** (test-enforced). |
| `api.py` | yes | `router = APIRouter(...)`; the only place for `fastapi`. |
| `schemas.py` | yes | Pydantic DTOs. Pure: no fastapi/sqlalchemy/pydantic_ai. |
| `services/` | yes | `__init__.py` + one module per service; orchestration, agents. |
| `models.py` | if it persists | SQLAlchemy ORM models on `shared.db.base.Base`. |
| `db.py` | iff `models.py` | Queries for this domain's tables only. |
| `logic/` | optional | Pure logic (solver, rules, math). See below. |
| `<subdomain>/` | optional | Same layout, nested. |

Anything else in a domain directory fails `test_domain_contains_only_known_files`.
Create a subdomain when a part has its own API, schemas and logic. Keep it in
the parent when it shares everything else with it.

`shared/<sub>/` is lighter: only `__init__.py` is required. The `db.py` iff
`models.py` rule still applies, and `fastapi` is still only allowed in `api.py`.

### Dependency rules (tests/architecture)

1. `shared` never imports a feature domain (transitive).
2. A feature domain may import another domain **only** through its `services`
   or `schemas` (direct imports checked; that domain's own services may use
   their own db). Subdomains belong to their top-level domain.
3. Only `api.py` modules (and `tuttitrip.main`) import `fastapi`/`starlette`.
4. Only `services` import `pydantic_ai`.
5. Only `models.py`, `db.py`, `services` and `shared.db` import `sqlalchemy`.
6. **Pure modules** = every module in a `logic/` package plus every
   `schemas.py`. They must not reach `fastapi`, `starlette`, `pydantic_ai`,
   `sqlalchemy`, `asyncpg` or `httpx`, even transitively. `logic` also must not
   reach `api`, `db`, `models`, `services`, `shared.db/config/auth` or `main`.
7. Every router in an `api.py` is registered in `tuttitrip.main.ROUTERS`.
8. Only `shared.jobs.services` imports `dbos`; `pgvector` only in
   models/db/services (both are also banned from pure modules).

pytest-archon notes (checked in its source): `should_not_import` is transitive
by default, `only_direct_imports=True` limits it, imports in functions and
`TYPE_CHECKING` blocks count, parent `__init__.py` execution is not modelled
(hence import-free `__init__.py`), and a rule matching nothing fails.
`tests/architecture/test_archon_semantics.py` pins this behaviour.

Cross-domain FKs use strings (`ForeignKey("trips.id")`), never imports.

## Conventions

- Ruff `select = ["ALL"]` with preview. Google docstrings on every public
  module, class and function, `Args:` when there are parameters and
  `Returns:`/`Yields:` when something comes back. Ignores live in
  `pyproject.toml` with a reason. Fix code instead of suppressing; if you
  must suppress, use a single-line `# ruff: ignore[rule-name]` with a reason.
- ty runs in strict mode (`all = "error"`). Only `models.py` relaxes
  `unsound-assignment` (SQLAlchemy `Mapped[...]` idiom).
- Tests never call real LLMs (`models.ALLOW_MODEL_REQUESTS = False` in
  `tests/conftest.py`); use `agent.override(model=TestModel(...))`.
- Domain tests go in `tests/domains/`, shared infrastructure in `tests/shared/`.
- Services raise domain exceptions; `api.py` maps them to HTTP errors.

## Settings and secrets

- All settings live in `shared/config/settings.py`: prefix `TUTTITRIP_`,
  nested delimiter `__` (e.g. `TUTTITRIP_DATABASE__HOST`).
- `.env.example` must list exactly the Settings fields, with comments
  (`tests/test_settings.py`). Adding a field means adding the line there.
- CORS: `TUTTITRIP_CORS_ORIGINS` (exact, default `http://localhost:5173`) plus
  `TUTTITRIP_CORS_ORIGIN_REGEX` (default: `tuttitrip.gburek.app` and
  `tuttitrip-develop.gburek.app`). Deploys can widen the regex through the
  `CORS_ORIGIN_REGEX` repo variable (Workers previews of `tuttitrip-frontend`).
- Auth0: tenant `dev-yahwm2zlut2gqdry.us.auth0.com`, API audience
  `https://tuttitrip-api.gburek.app`, SPA application "TuttiTrip Web". The API
  validates RS256 access tokens (JWKS, issuer, audience, exp).
- Provider API keys (e.g. `OPENAI_API_KEY`) are read by Pydantic AI under
  their own names and are not Settings fields.
- Never commit secrets or `.env`. CI/deploy secrets are GitHub Actions
  secrets/variables; host-only secrets live in `~/tuttitrip/*.env` on the host.

## Database and migrations

- PostgreSQL 18 with pgvector 0.8.7 (`pgvector/pgvector:0.8.7-pg18-trixie`,
  same image locally and on the host); the `vector` extension is enabled by a
  migration.
- Async SQLAlchemy 2 + asyncpg; sessions via `SessionDep`; services commit.
- `uv run alembic revision --autogenerate -m "..."` (models are discovered
  automatically), review the file, then `uv run alembic upgrade head`.
- The app never migrates on startup. A one-off `alembic upgrade head` runs
  before the new container starts (compose `migrate` service, `deploy/deploy.sh`).

## Background jobs and tuttitrip-worker

Long-running work (LLM agents, embeddings, enrichment) runs in a separate repo,
[tuttitrip-worker](https://github.com/HackYeah-TuttiTripTeam/tuttitrip-worker),
as DBOS workflows. This backend only enqueues jobs and reads their state
through `DBOSClient` (`src/tuttitrip/shared/jobs/`). Full rules are in
`deploy/CONVENTIONS.md`, section "Integracja z workerem". The short version:

- The contract (workflow and queue names, payloads, events, `CONTRACT_VERSION`)
  is canonical in the worker. The mirror is `shared/jobs/contracts.py`, rendered
  to `contracts/jobs.schema.json` (a test keeps them in sync). CI job
  `contracts-check` diffs it against the worker's file (same branch, else
  develop, else main) using the `WORKER_REPO_TOKEN` secret.
- Enqueue from a feature service: `await queue.enqueue(Workflow.X, XInput(...),
  user=user.sub, key=<domain id>)`, with `queue: JobQueueDep` injected in
  `api.py`. Call `ensure_worker_available(session)` first. Return `JobAccepted`
  (202), and the client polls `GET /jobs/{id}` (`POST /jobs/{id}/cancel` cancels).
- Payloads are small (ids and parameters) and travel as portable JSON with a
  deterministic workflow id (idempotent), a timeout and the env's app version.
- Incompatible contract change: (1) the worker accepts old+new, (2) the backend
  bumps `CONTRACT_VERSION` and regenerates the JSON, (3) the worker drops the old
  version. Use the `sync-contracts` skill.
- The backend owns all DDL: Alembic for app tables, `dbos migrate` for the DBOS
  schema. The worker uses role `tuttitrip_worker` (no DDL). Tables it may write
  are listed in `deploy/worker-grants.sql`.
- `/health` reports `worker: ok|stale|missing` from `worker_heartbeats` and
  turns `degraded` on an incompatible contract. Enqueue endpoints return 503
  while the worker is missing.
- Every deploy runs a `ping` job through the worker (smoke test). If the env
  has no worker image yet, the deploy starts one from `:develop` or `:main`.
- Locally: `docker compose up -d --wait db && uv run alembic upgrade head &&
  uv run dbos migrate -s postgresql://tuttitrip:tuttitrip@localhost:5432/tuttitrip`,
  then start the worker from its repo with the same `DBOS_SYSTEM_DATABASE_URL`
  and `DBOS__APPVERSION=local`.

## Zgłoszenia, PR i wydania

Zasady są wspólne dla całej organizacji, pełny opis jest w
[CONTRIBUTING.md](https://github.com/HackYeah-TuttiTripTeam/.github/blob/main/CONTRIBUTING.md).

Zgłoszenia (issues):

- Tytuł zaczyna się od `feat:`, `docs:`, `chore:` albo `bug:`, opcjonalnie
  z zakresem, np. `feat(backend): Eksport planu do PDF`. Regex:
  `^(feat|docs|chore|bug)(\([a-z0-9-]+\))?: \S.{3,}`.
- Treść ma sekcje `###` i żadna wymagana nie może być pusta. W `feat`,
  `docs` i `chore` są to Opis, Dlaczego, Kryteria akceptacji, Definition of
  Done i Obszar (w `feat` można dodać Poza zakresem). W `bug` są to Opis,
  Kroki do odtworzenia, Oczekiwane zachowanie, Faktyczne zachowanie,
  Środowisko, Dlaczego, Kryteria akceptacji, Definition of Done i Obszar.
- `.github/workflows/issue-format.yml` sprawdza każde nowe i edytowane
  zgłoszenie. Złe zamyka jako "not planned", dodaje etykietę
  `invalid-format` i pisze w komentarzu, co poprawić. Po poprawce otwiera je
  ponownie. Ustawia też etykietę `type:*`.
- Z terminala (skill `new-issue` przygotuje treść i założy zgłoszenie):

  ```bash
  gh issue create --title "feat(backend): Eksport planu do PDF" --body-file - <<'MD'
  ### Opis
  Organizator pobiera gotowy plan jako PDF.

  ### Dlaczego
  W podróży plan musi być dostępny offline, a nie każdy instaluje PWA.

  ### Kryteria akceptacji
  - [ ] Given gotowy plan, When kliknę "Pobierz PDF", Then dostanę plik z planem dzień po dniu

  ### Definition of Done
  - [ ] CI zielone (lint, typy, testy, testy architektury)
  - [ ] PR zmergowany do `develop` i sprawdzony na wdrożeniu develop

  ### Obszar
  Backend
  MD
  ```

Pull requesty i merge:

- Tytuł PR: `feat:`, `docs:`, `chore:` albo `bugfix:` (w PR nie `bug:`),
  opcjonalnie z zakresem. PR wydania `develop` -> `main` ma tytuł
  `release: opis`.
- Opis po polsku według szablonu: `## Co i dlaczego`, `## Powiązane issue`
  (`Closes #12` albo `Refs #12`; w `docs` i `chore` może być `brak`),
  `## Lista zmian`, `## Jak przetestować`, `## Zrzuty ekranu`,
  `## Checklista`. Gotowy szablon ma skill `open-pr`.
- `.github/workflows/pr-format.yml` oznacza check na czerwono i komentuje,
  gdy tytuł albo sekcje są złe. Bez ochrony gałęzi (darmowy plan) czerwony
  check nie blokuje merge'a, więc nie mergujemy z czerwonym.
- PR do `develop` mergujemy przez "Squash and merge" (tytuł PR staje się
  commitem). PR wydania do `main` mergujemy przez "Create a merge commit".
  GitHub nie pozwala ustawić metody osobno dla gałęzi, więc to zasada
  zespołu.

Wydania:

- `.github/workflows/release-notes.yml` (Release Drafter, konfiguracja w
  repozytorium `.github`) nadaje PR etykietę `type:*` według prefiksu tytułu
  i po każdym merge'u do `develop` aktualizuje szkic następnego wydania w
  GitHub Releases.
- Merge PR `release:` do `main` publikuje szkic i zakłada tag `vX.Y.Z`.
  `feat` podnosi wersję minor, pozostałe typy patch, pierwsze wydanie to
  `v0.1.0`. Nie prowadzimy pliku CHANGELOG.md.

## Git flow

- `main` is production; `develop` is integration. Both change only through
  PRs with green `checks` and `contracts-check`, with no force-push and no
  deletion (0 required approvals: a 5-person, 24 h team, so CI is the gate).
  GitHub cannot enforce this for a private repo on the org's free plan
  (branch protection and rulesets both return 403), so it is a team rule
  until the org upgrades. Then apply it with the API call in the README.
- Branch from `develop`: `feature/<short-name>`, `fix/<short-name>`,
  `chore/<short-name>`. PR into `develop`; release = PR `develop` -> `main`.
- Head branches are deleted automatically after merge.
- No AI attribution in commits, PRs or docs (no `Co-Authored-By` trailers
  for assistants, no "generated with" lines).

## Deployment

`/openapi.json` and `/docs` are public on every deployment (the frontend
generates its client from them). Every push runs CI (`checks` on the org runners `[self-hosted, hackathon]`),
then `deploy` on the runner installed on the host (`[self-hosted, tuttitrip-deploy]`).

| Branch | URL | Database |
| --- | --- | --- |
| `main` | https://tuttitrip-api.gburek.app | `tuttitrip_main` (kept) |
| `develop` | https://tuttitrip-api-develop.gburek.app | `tuttitrip_develop` (kept) |
| any other | https://tuttitrip-api-<slug>.gburek.app | `tuttitrip_br_<slug>` (dropped with the branch) |

Slug: lowercase, every run of non-alphanumerics becomes `-`, trimmed, label
capped at 63 chars (`feature/cos tam` -> `tuttitrip-api-feature-cos-tam`).
Naming lives in `deploy/lib.sh` and is tested in `tests/test_deploy_naming.py`.

On the host, everything is namespaced: Docker network `tuttitrip`, containers
`tuttitrip-postgres` (pgvector image, volume `tuttitrip-postgres-data`), `tuttitrip-gateway` (nginx on `172.17.0.1:18080`, routes
by Host header) and `tuttitrip-api[-<slug>]`, label `tuttitrip.managed=true`.
The shared Cloudflare tunnel is remotely managed: deploy inserts our hostname
before the wildcard rule via the API (backups in `~/tuttitrip/backups/`).
Cleanup (every deploy + on branch deletion) removes containers, images,
feature databases and ingress for branches that no longer exist. Never touch
host resources outside this namespace.
