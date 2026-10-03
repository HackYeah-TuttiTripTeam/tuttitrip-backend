# AGENTS.md

Canonical instructions for anyone (human or agent) changing this repository.
`CLAUDE.md` imports this file. The user-facing docs are in `README.md` (Polish).

## What this is

TuttiTrip (working name WARTO) is the backend of a group/family trip planner
built at HackYeah 2026. FastAPI + Pydantic + Pydantic AI on Python 3.14, async
SQLAlchemy 2 on PostgreSQL, Auth0 for login. The web client consumes the
OpenAPI schema (`/api/v1/openapi.json`) through a generated TypeScript client.

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
    auth/              Auth0 JWT verification, CurrentUser (authentication only)
    permissions/       feature registry, roles/grants, requires(), GET /api/v1/me, admin API
    health/            GET /api/v1/health (DB + worker), GET /api/v1/health/live
    llm/               Pydantic AI model catalog (services/model_catalog.py)
    jobs/              DBOS client: enqueue/status/cancel worker jobs, contract mirror
  trips/               reference slice: api -> services -> db -> models; TripAccess
  profiles/            people on a trip (weights, age groups)
  interview/           AI interview agent (AG-UI endpoint goes here)
  planning/            planner agent; subdomains fairness/ and linter/
                       algorithm spec (canonical for planning/**/logic): docs/algorytm.md
  accommodation/       requirements contract (met/unmet/unconfirmed)
  expenses/            expenses; subdomain settlement/
  search/              pgvector embeddings (written by the worker)
  places/              city and place catalog; prices and hours carry source + verified mark
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
   their own db). Subdomains belong to their top-level domain. Exception for
   HTTP dependencies: an `api.py` may import another domain's `api.py` (e.g.
   `TripAccess` from `trips.api`); nothing else imports an `api` module.
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

## API paths

- Every endpoint lives under `/api/v{N}/`, today `/api/v1/` (`API_PREFIX` in
  `main.py`), including `/api/v1/health`, `/api/v1/me`, `/api/v1/jobs/...`,
  `/api/v1/openapi.json`, `/api/v1/docs` and `/api/v1/redoc`. Routers in
  `api.py` keep their own short prefix (`/trips`); `create_app()` adds the
  version once. `tests/architecture/test_routes.py` fails on any route outside
  it (allow-list `ALLOWED_UNVERSIONED`, empty on purpose).
- Route-level tests iterate `fastapi.routing.iter_route_contexts(app.routes)`:
  FastAPI 0.142 includes routers lazily, so `app.routes` alone does not list them.
- The deployed frontends call the API same-origin through their Worker proxy
  (`https://tuttitrip[-develop].gburek.app/api/...`), so a browser never needs CORS
  there; CORS still matters for direct cross-origin use. It allows no
  credentials (the API takes bearer tokens, never cookies).
- Old unversioned paths (`/health`, `/openapi.json`, `/docs`, `/trips`...) answer 404.

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
- Validation errors (422) never echo request values: `shared/errors/api.py`
  strips `input` and `ctx` from every item. This protects secrets in headers
  and bodies and large pasted texts; do not add a handler that returns them.

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
- Admins: the Auth0 post-login Action puts `["admin"]` into the namespaced
  access-token claim `https://tuttitrip.gburek.app/roles`
  (`TUTTITRIP_AUTH0__ROLES_CLAIM`) for people on the superadmin allow-list
  (kept in Auth0 and host env files, never in a repo). `CurrentUser.roles` /
  `.is_admin` expose it; the permission system turns it into `*` WRITE
  (section "Uprawnienia"). `GET /api/v1/me` returns `roles`, `is_admin` and
  `access`.
- Models: agents use catalog ids (`tuttitrip:agent`, `chat`, `decide`,
  `decide-laya`, `decide-cloud`, `openrouter`) with `catalog.capability()`,
  never `provider:model` strings. The GB10 key is `TUTTITRIP_LLM__GB10_API_KEY`;
  OpenRouter reads `OPENROUTER_API_KEY` when `TUTTITRIP_LLM__OPENROUTER_API_KEY`
  is empty. A chain link without its key is skipped; a chain with no key raises
  `UserError`. Live check outside CI: `uv run python scripts/smoke_llm.py`.
- Other provider API keys are read by Pydantic AI under their own names and are
  not Settings fields.
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
  (202), and the client polls `GET /api/v1/jobs/{id}` (`POST /api/v1/jobs/{id}/cancel` cancels).
- Payloads are small (ids and parameters) and travel as portable JSON with a
  deterministic workflow id (idempotent), a timeout and the env's app version.
- Incompatible contract change: (1) the worker accepts old+new, (2) the backend
  bumps `CONTRACT_VERSION` and regenerates the JSON, (3) the worker drops the old
  version. Use the `sync-contracts` skill.
- The backend owns all DDL: Alembic for app tables, `dbos migrate` for the DBOS
  schema. The worker uses role `tuttitrip_worker` (no DDL). Tables it may write
  are listed in `deploy/worker-grants.sql`.
- `/api/v1/health` reports `worker: ok|stale|missing` from `worker_heartbeats` and
  turns `degraded` on an incompatible contract. Enqueue endpoints return 503
  while the worker is missing.
- Every deploy runs a `ping` job through the worker (smoke test). If the env
  has no worker image yet, the deploy starts one from `:develop` or `:main`.
- Locally: `docker compose up -d --wait db && uv run alembic upgrade head &&
  uv run dbos migrate -s postgresql://tuttitrip:tuttitrip@localhost:5432/tuttitrip`,
  then start the worker from its repo with the same `DBOS_SYSTEM_DATABASE_URL`
  and `DBOS__APPVERSION=local`.

## Uprawnienia

Ścieżki w tej sekcji są względne wobec `API_PREFIX` (`/api/v1`). Dwie
warstwy, sprawdzane niezależnie:

1. **Uprawnienia do funkcjonalności** (globalne możliwości): `READ` albo
   `WRITE` na węźle drzewa funkcjonalności. `WRITE` obejmuje `READ`. Kod w
   `src/tuttitrip/shared/permissions/`.
2. **Dostęp do obiektu** (czy *ten* wyjazd jest twój): role na wyjeździe
   `member < co_host < host` w tabeli `trip_members`. Twórca wyjazdu jest
   hostem. Sprawdza to domena (`TripAccess`, serwisy), nie system uprawnień.

`trips.core:WRITE` znaczy „może tworzyć i edytować wyjazdy w ogóle”. Czy może
edytować wyjazd X, decyduje jego rola na X.

### Drzewo funkcjonalności

- Rejestr jest w kodzie: `shared/permissions/registry.py`, enum `Feature`
  (kod z kropkami odpowiada domenie i poddomenie) i `DESCRIPTIONS` (polskie
  opisy dla panelu admina i OpenAPI). Korzeń to `*`.
- Rodzicem `a.b` jest `a`, rodzicem `a` jest `*`. Uprawnienie na węźle
  obejmuje całe poddrzewo: `trips:WRITE` daje `WRITE` na `trips.core`,
  `trips.members` i `trips.invitations`.
- Endpointy wymagają tylko liści. Własne dane grupy mają liść `<grupa>.core`
  (np. `trips.core`, `profiles.core`, `expenses.core`).
- Wszystko, co tylko dla administratorów, jest pod `admin.*`
  (`admin.permissions`, `admin.users`, `admin.planning_weights`).
- Efektywny poziom = maksimum ze wszystkich uprawnień (role, domyślna rola
  `user`, uprawnienia bezpośrednie, claim superadmina) na danym węźle albo
  jego przodku. Kody spoza rejestru (np. usuniętej funkcji) są ignorowane.
- Uprawnienia liczymy raz na żądanie (jedno zapytanie, cache zależności
  FastAPI w obrębie żądania), nigdy między żądaniami i nigdy z tokenu.

Nowy węzeł (skill `new-permission`):

1. Dodaj członka do `Feature` i opis w `DESCRIPTIONS` (testy sprawdzają opis
   i istnienie rodzica).
2. Jeśli zwykły użytkownik ma go mieć, dodaj migrację, która wstawia wiersz
   do `role_grants` roli `user` (tylko liście spoza `admin.*`). Seed w starej
   migracji jest zamrożony.
3. Nigdzie nie pisz kodu jako tekstu (`"trips.core"`), zawsze
   `Feature.TRIPS_CORE`. Test architektury to wyłapie.

### Ochrona endpointu

Każdy endpoint ma dokładnie jeden znacznik w `dependencies=[...]`:

```python
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature

@router.get("", dependencies=[requires(Feature.TRIPS_CORE, Access.READ)])
```

- Bez tokenu 401, bez uprawnienia 403 `Missing permission trips.core:READ`.
- `public()` (bez logowania) tylko dla `/api/v1/health`, `/api/v1/health/live` i smoke
  testu `/api/v1/jobs/ping*`. Lista jest w `tests/architecture/test_permissions.py`;
  `/api/v1/docs`, `/api/v1/openapi.json` i `/api/v1/redoc` są publiczne z definicji.
- `tests/architecture/test_permissions.py` przechodzi po `create_app().routes`
  (z zależnościami routerów) i nie przepuści trasy bez znacznika, z dwoma
  znacznikami, z grupą zamiast liścia ani ze stringiem zamiast `Feature.X`.
- OpenAPI dostaje `x-required-permission: "trips.core:READ"` (albo
  `x-public: true`), linijkę „Wymagane uprawnienie” w opisie i odpowiedzi
  401/403, więc widać to w `/api/v1/docs` i w kliencie TS.
- Gdy decyzja zależy od uprawnienia w środku logiki, `api.py` wstrzykuje
  `EffectivePermissionsDep` i przekazuje obiekt do serwisu, który woła
  `permissions.allows(Feature.X, Access.WRITE)`.

### Role

| Rola | Uprawnienia | Uwagi |
| --- | --- | --- |
| `user` | liście bez `admin.*`: `WRITE` na `accounts.profile`, `trips.*`, `profiles.*`, `interview`, `planning.proposals`, `planning.plans`, `accommodation`, `expenses.core`, `jobs`; `READ` na `planning.fairness`, `planning.linter`, `search`, `places.catalog`, `expenses.settlement` | Ma ją każdy zalogowany bez przypisania. Admin może ją edytować, ale tylko liśćmi spoza `admin.*`. |
| `superadmin` | `*:WRITE` | Tylko z claimu Auth0 `admin` (lista osób jest w Akcji Auth0). API jej nie przypisze ani nie zmieni, a wiersz w bazie jest ignorowany. Nowe funkcjonalności obejmuje automatycznie (test). |
| własne | dowolne | `POST /admin/permissions/roles`. |

- Tabele: `roles`, `role_grants`, `user_roles`, `user_grants` (użytkownik =
  Auth0 `sub`, bez tabeli użytkowników) i `permission_audit`. Rola workera nie
  ma do nich dostępu (`deploy/worker-grants.sql`, test).
- Nikt nie nada więcej, niż sam ma: dotyczy grantów roli, przypisania roli
  i uprawnień bezpośrednich (403). Zmiany wymagają `admin.permissions:WRITE`.
- Każda zmiana trafia do `permission_audit` (kto, co, komu, kiedy) w tej samej
  transakcji. Trigger blokuje `UPDATE`, `DELETE` i `TRUNCATE` tej tabeli.
- API admina (`admin.permissions` READ/WRITE): `GET /admin/permissions/features`
  (drzewo), `roles` (lista, `POST`, `PUT`/`DELETE {name}`), `users`
  (`GET {sub}`, `PUT`/`DELETE {sub}/roles/{role}`,
  `PUT`/`DELETE {sub}/grants/{feature}`), `audit`.
- Dlaczego `shared/permissions`, a nie domena `accounts`: znacznika `requires`
  używa każdy router, także te w `shared` (`/me`, `/jobs`), a `shared` nie
  może importować domen. Autoryzacja zależy od uwierzytelniania (`shared.auth`),
  nigdy odwrotnie, dlatego `/me` jest w `shared/permissions/api.py`.

### Dostęp do obiektu (wyjazdy)

- Każda trasa z `{trip_id}` w ścieżce zależy od `TripAccess(min_role)` z
  `tuttitrip.trips.api` (aliasy `TripMember`, `TripCoHost`, `TripHost`).
  Test architektury to wymusza.
- Ktoś spoza wyjazdu dostaje 404 (nie zdradzamy, że wyjazd istnieje), ktoś
  z za niską rolą 403.
- `TripAccess` zwraca `TripMembership`. Przekaż go do serwisu jako dowód
  sprawdzenia (`list_profiles(session, membership)`).
- Gdy `trip_id` przychodzi w treści żądania, serwis woła
  `trip_service.get_membership(session, trip_id, sub, TripRole.X)` sam (np.
  generowanie planu wymaga `co_host`).
- `TripRead.my_role` mówi frontendowi, jaką rolę ma użytkownik na wyjeździe.
- Nowy zasób z właścicielem (inny niż wyjazd): ten sam wzorzec w jego domenie,
  czyli tabela członkostwa albo `owner_sub`, serwis z `get_membership`
  i zależność w `api.py`. Bez ogólnych ACL per obiekt.

## Design system

Skill `tuttitrip-design-system` (`.claude/skills/tuttitrip-design-system`) jest wspólny dla wszystkich
repozytoriów TuttiTrip; UI powstaje we frontendzie. W backendzie teksty, które czyta człowiek (komunikaty
błędów pokazywane w aplikacji, opisy narzędzi MCP, eksport planu), piszemy według słownika UI z README
skilla: „sprawdzenie planu” i „problemy”, a nie „linter” i „naruszenia”; werdykty „Obowiązkowo”, „Pasuje”,
„Kultowe, ale nie Twoje”, „Pomiń”; powody „Za drogo”, „Za daleko” i tak dalej. Kody reguł i nazwy
komponentów nie trafiają do tekstów dla ludzi.

## Praca agentów nad issues

Nad backlogiem pracuje równolegle kilku agentów AI i ludzi. Te zasady pilnują, żeby nikt nie wchodził
innym w drogę i żeby każda funkcja przeszła ten sam proces. Dotyczą też ludzi.

1. Wybór issue. Bierzesz tylko issue z tablicy
   [TuttiTrip](https://github.com/orgs/HackYeah-TuttiTripTeam/projects/1) ze statusem Todo, bez etykiety
   `in-progress` i bez przypisanej osoby. Linia „Zależy od:” w opisie wymienia issues, które muszą być
   zmergowane do `develop`. Jeśli któreś nie jest, pracuj tylko na jego kontrakcie (np. stała odpowiedź z
   OpenAPI) i napisz to w komentarzu. Kolejność: najpierw P0, potem P1, w obrębie milestone'u.
2. Zajęcie issue, zanim napiszesz kod:
   - `gh issue edit <nr> --add-label in-progress`,
   - Status na tablicy: In Progress,
   - komentarz „Start” z nazwą gałęzi, ścieżką worktree i krótkim planem (pliki, które zmienisz).
   Etykieta `in-progress` znaczy „zajęte”. Nie bierz takiego issue i nie zmieniaj go bez zgody zespołu.
3. Worktree i gałąź. Nigdy nie pracuj w głównym klonie repozytorium. Jedno issue to jeden worktree, jedna
   gałąź i jeden PR do `develop`:

   ```bash
   git -C ~/Documents/GitHub/<repo> fetch origin
   git -C ~/Documents/GitHub/<repo> worktree add -b feature/<nr>-<krotka-nazwa> \
     ~/Documents/GitHub/worktrees/tuttitrip/<repo>-<nr>-<krotka-nazwa> origin/develop
   ```

   (`<repo>` to `tuttitrip-backend`, `tuttitrip-worker` albo `tuttitrip-frontend`; w repo zbiorczym
   `tuttitrip` gałąź bierzesz z `origin/main`.)
4. Komentarze ze statusem w issue po każdym etapie: plan, implementacja z testami, wynik smoke testu,
   wynik review subagenta, link do PR. Krótko: co zrobione, co dalej, co blokuje. Gdy utkniesz: etykieta
   `blocked` i komentarz z powodem i tym, czego potrzebujesz.
5. Pliki wspólne, w których łatwo o konflikt, zmieniaj małymi krokami i przed PR rób
   `git fetch origin && git rebase origin/develop`:
   - `src/tuttitrip/main.py` (`ROUTERS`) i `shared/permissions/registry.py` (`Feature`, migracje z rolą `user`),
   - migracje Alembic: jedna głowa; przed PR przepnij `down_revision` na aktualną głowę `develop`
     i uruchom `uv run alembic heads`,
   - `shared/config/settings.py` razem z `.env.example`,
   - `shared/jobs/contracts.py` i `contracts/jobs.schema.json` (najpierw worker, skill `sync-contracts`).
6. Smoke test jest obowiązkowy dla KAŻDEGO zrealizowanego feature'a. Po pushu gałęzi poczekaj na
   wdrożenie podglądu i przejdź na żywo scenariusz z kryteriów akceptacji issue:
   - każdy push gałęzi wdraża API pod `https://tuttitrip-api-<slug>.gburek.app/api/v1/...`
     (Swagger: `/api/v1/docs`); wołaj endpointy z tokenem konta testowego i sprawdź `/api/v1/health`,
   - dla endpointów z uprawnieniami sprawdź też 401 bez tokenu, 403 bez uprawnienia i 404 dla cudzej
     podróży.
   Wynik (kroki, odpowiedzi albo zrzuty ekranu) wpisz w komentarzu w issue. Bez zielonego smoke testu
   nie ma PR.
7. Review subagenta. Po zielonym smoke teście uruchom subagenta-recenzenta z diffem gałęzi, treścią
   issue i story źródłową. Sprawdza:
   - uproszczenie kodu i zbędną złożoność (skille `simplify` i `ponytail-review`),
   - złożoność logiki,
   - poprawność biznesową względem story, słownika z dokumentu architektonicznego i, przy logice
     planowania, specyfikacji algorytmu (`docs/algorytm.md` w tuttitrip-backend).
   Popraw to, co znalazł, i **powtórz smoke test**. Wynik review i drugiego smoke testu wpisz w komentarzu.
8. PR. Dopiero po tym otwórz PR do `develop` skillem `open-pr` (`Closes #<nr>`) i ustaw Status: In
   Review. Po merge'u zdejmij `in-progress`, usuń worktree
   (`git -C ~/Documents/GitHub/<repo> worktree remove <ścieżka>`); zamknij issue ręcznie
   (`gh issue close <nr> --comment "Zmergowane w #<PR>"`), bo `Closes` zamyka issue dopiero po merge'u
   do `main` (wydanie). Status: Done.

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

## Places catalog conventions

- Every price and every opening-hours entry has a source and a `verified`
  mark; the database refuses `verified` without `source_url` and `checked_at`
  (and, for hours, without `opening_hours`).
- Free admission is a `place_prices` row with `amount` 0 and `verified` true.
  No row means the price is unknown (unverified, the delta in E6).
- Concessions: `age_min`/`age_max` on the price row; when null the defaults
  are child up to 17, senior from 65, family 2+2 (`family_size` overrides).
- `unit` says what the price is for: `person`, `night` (lodging, E6 multiplies
  by the nights) or `group`.
- `indoor` and `wheelchair` are null when unknown. Tags, diet tags, amenities
  and cuisine are the StrEnums in `places/schemas.py` (`PlaceTag`, `DietTag`,
  `Cuisine`, `Amenity`), mirrored by CHECK constraints; add a value in both
  the enum and a migration.
- Money is `Decimal` in the code and a string in the API (`"35.00"`).

## Git flow

- `main` is production; `develop` is integration. Both change only through
  PRs with green `checks` and `contracts-check`, with no force-push and no
  deletion (0 required approvals: a 5-person, 24 h team, so CI is the gate).
  GitHub cannot enforce this for a private repo on the org's free plan
  (branch protection and rulesets both return 403), so it is a team rule
  until the org upgrades. Then apply it with the API call in the README.
- Branch from `develop`: `feature/<short-name>`, `fix/<short-name>`,
  `chore/<short-name>`. PR into `develop`; release = PR `develop` -> `main`.
- After a merge the `Delete merged branch` workflow
  (`.github/workflows/delete-merged-branch.yml`, logic in the org `.github`
  repo) deletes the head branch and starts `cleanup.yml`, which removes its
  preview deployment. It never deletes `main` or `develop` (nor forks, PRs
  closed without a merge or branches that are the base of another open PR),
  so release PRs go straight from `develop`. GitHub's "Automatically delete
  head branches" stays off: without branch protection it would also delete
  `develop` when a release PR is merged. If `develop` disappears anyway,
  recreate it at the release PR's head commit; deploy cleanup refuses to run
  while `main` or `develop` is missing.
- No AI attribution in commits, PRs or docs (no `Co-Authored-By` trailers
  for assistants, no "generated with" lines).

## Deployment

`/api/v1/openapi.json` and `/api/v1/docs` are public on every deployment (the
frontend generates its client from them). Every push runs CI (`checks` on the org runners `[self-hosted, hackathon]`),
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

Admin tools (`deploy/admin/`, details in `deploy/CONVENTIONS.md`):
https://tuttitrip-pgadmin.gburek.app (pgAdmin, read-only role
`tuttitrip_readonly`) and https://tuttitrip-dbos.gburek.app (DBOS dashboard
from tuttitrip-worker). Both sit behind their own nginx
(`tuttitrip-admin-gateway`, `172.17.0.1:18081`) with `auth_request` to
oauth2-proxy (Auth0 app "TuttiTrip Admin (oauth2-proxy)") and the superadmin
allow-list. The list lives in the Auth0 Action secrets and
`~/tuttitrip/admin.env`, never in a repo. `deploy/admin/setup.sh` (idempotent)
sets everything up and runs after every deploy of `main`. The Auth0 Action
"TuttiTrip superadmins" is maintained in the Auth0 dashboard (the source of
truth; the MCP has no `actions` scopes). `deploy/admin/auth0-post-login.js` is
a 1:1 copy of it. To change it, edit the copy, run
`node --test deploy/admin/test-auth0-action.mjs`, then paste it into the
dashboard and deploy. Its secrets are `ALLOWED_EMAILS`, `ALLOWED_DISCORD_IDS`
and `ALLOWED_USER_IDS`; only these names go into the repo.

## Powiadomienia (Discord)

- `.github/workflows/discord-notify.yml` wysyła na Discord zespołu wynik
  każdego innego workflow tego repozytorium (`workflow_run: completed`) przez
  wspólny `discord-notify.yml` z repozytorium
  [`.github`](https://github.com/HackYeah-TuttiTripTeam/.github). GitHub
  uruchamia go tylko z kopii na `main`, ta na `develop` jest dla porządku.
- Nowy albo przemianowany workflow trzeba dopisać po nazwie (`name:`) do
  listy `workflows:` w tym pliku.
- Zasady szumu:
  - `skipped` nie idzie wcale,
  - `Issue format`, `PR format`, `Delete merged branch`, `Release notes` i sprzątanie (`Cleanup branch deployments`) piszą tylko przy niepowodzeniu,
  - sukces na `main` i `develop` to pełna wiadomość z adresem API (https://tuttitrip-api.gburek.app, https://tuttitrip-api-develop.gburek.app),
  - sukces na innej gałęzi i anulowanie to jedna linia (po wdrożeniu gałęzi z adresem `https://tuttitrip-api-<slug>.gburek.app`),
  - błąd to zawsze pełna wiadomość z listą nieudanych jobów.
- Zmiany w projekcie #1 oraz nowe, zamknięte i scalone issue i PR wysyła
  Worker `tuttitrip-discord-relay` z webhooka organizacji (kod w
  `.github/discord-relay`), nie ten workflow.
- Webhook to sekret repozytorium `DISCORD_WEBHOOK_URL` (sekret organizacji
  nie działa: na darmowym planie nie widzą go repozytoria prywatne). Rotacja:
  nowy webhook w Discordzie, `gh secret set DISCORD_WEBHOOK_URL` w czterech
  repozytoriach i `wrangler secret put DISCORD_WEBHOOK_URL` w Workerze.
  Szczegóły w
  [CONTRIBUTING.md](https://github.com/HackYeah-TuttiTripTeam/.github/blob/main/CONTRIBUTING.md#powiadomienia-discord).
  URL-a webhooka nie wklejamy nigdzie (issue, PR, logi, czat).
