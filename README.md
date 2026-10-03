# TuttiTrip: backend

Backend aplikacji **TuttiTrip** (robocza nazwa WARTO), którą pięcioosobowy zespół
buduje na hackathonie **HackYeah 2026**. To planer wyjazdów rodzinnych i grupowych.
Organizator przechodzi wywiad z asystentem AI, a aplikacja tworzy z niego profile
wszystkich uczestników. Następnie deterministyczny solver układa plan, który
dzieli zadowolenie po równo (maksymalizuje sumę logarytmów użyteczności). Linter
planu sprawdza godziny otwarcia, dystanse, budżet i dostępność. Do tego dochodzą
wymagania dotyczące noclegu, wydatki z rozliczeniem i przeplanowanie w trakcie
wyjazdu.

Model językowy prowadzi rozmowę i pisze szkice planów. O planie decyduje czysty
kod (solver, linter, reguły cenowe, rozliczenie), a testy architektury pilnują,
żeby ten kod nie importował FastAPI, Pydantic AI ani bazy danych.

Frontend (React) jest w osobnym repozytorium `tuttitrip-frontend` i generuje
klienta TypeScript z `/openapi.json` tego API.

## Stack

| Obszar | Narzędzie |
| --- | --- |
| Język | Python 3.14 |
| Projekt i zależności | [uv](https://docs.astral.sh/uv/) |
| API | [FastAPI](https://fastapi.tiangolo.com/) + [Pydantic](https://docs.pydantic.dev/) |
| Agenci AI | [Pydantic AI](https://ai.pydantic.dev/) |
| Baza danych | PostgreSQL 18 + pgvector 0.8.7, SQLAlchemy 2 (async, asyncpg), Alembic |
| Zadania w tle | [DBOS](https://docs.dbos.dev/) (klient; workflowy wykonuje [tuttitrip-worker](https://github.com/HackYeah-TuttiTripTeam/tuttitrip-worker)) |
| Konfiguracja | pydantic-settings |
| Logowanie | Auth0 (Google, Discord), tokeny JWT RS256 |
| Jakość | Ruff (`ALL` + preview), ty (wszystkie reguły jako błędy), pytest, pytest-archon |
| CI/CD | GitHub Actions na self-hosted runnerach, Docker, Cloudflare Tunnel |

## Wymagania

- [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.12 lub nowszy.
  Jeśli nie masz Pythona 3.14, uv go zainstaluje (`uv python install 3.14`).
- Docker z Docker Compose (lokalna baza PostgreSQL).
- Opcjonalnie klucz API dostawcy modeli (np. `OPENAI_API_KEY`), jeśli chcesz
  uruchamiać agentów naprawdę. Testy nie wysyłają żadnych zapytań do modeli.

## Uruchomienie lokalne

### 1. Zależności i konfiguracja

```bash
uv sync                 # tworzy .venv razem z grupą dev
cp .env.example .env    # lokalne ustawienia; opis każdej zmiennej jest w pliku
```

Każde ustawienie ma prefiks `TUTTITRIP_`, a pola zagnieżdżone oddziela podwójne
podkreślenie, na przykład `TUTTITRIP_DATABASE__HOST`. Test pilnuje, żeby
`.env.example` miał dokładnie te same pola co klasa `Settings`.

Jeśli port 5432 jest zajęty, ustaw w `.env` `TUTTITRIP_DATABASE__PORT=5433`.
Docker Compose wystawi wtedy bazę na tym porcie i aplikacja połączy się z nim.

### 2. Baza danych i migracje

```bash
docker compose up -d --wait db    # PostgreSQL 18 z pgvector
uv run alembic upgrade head       # tabele i rozszerzenie vector
uv run dbos migrate -s postgresql://tuttitrip:tuttitrip@localhost:5432/tuttitrip   # schemat DBOS dla workera
```

Przy innym porcie bazy zmień go też w adresie dla `dbos migrate`.

### 3. API

```bash
uv run uvicorn tuttitrip.main:app --reload
```

- http://localhost:8000/docs: dokumentacja Swagger
- http://localhost:8000/openapi.json: schemat dla generatora klienta TS
- http://localhost:8000/health: stan aplikacji, bazy i workera (`503`, gdy baza nie odpowiada albo worker ma niezgodną wersję kontraktu)
- http://localhost:8000/me: dane zalogowanego użytkownika (wymaga tokenu Auth0)

Całość w kontenerach (baza, jednorazowe migracje, API):

```bash
docker compose up --build
```

### 4. Testy, lint i typy

```bash
uv run pytest                 # testy, w tym testy architektury
uv run ruff check .           # lint (uv run ruff check --fix . poprawia, co się da)
uv run ruff format .          # formatowanie
uv run ty check               # typy
```

Przed każdym commitem (CI sprawdza to samo):

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
```

### Nowa migracja

```bash
uv run alembic revision --autogenerate -m "opis zmiany"
# przejrzyj plik w migrations/versions/, potem:
uv run alembic upgrade head
```

## Worker i zadania w tle

Długie operacje (agenci LLM, embeddingi w pgvector, wzbogacanie danych)
wykonuje osobna usługa
[tuttitrip-worker](https://github.com/HackYeah-TuttiTripTeam/tuttitrip-worker)
jako workflowy DBOS. Backend tylko dodaje zadania do kolejki i odczytuje ich
stan przez `DBOSClient` (`src/tuttitrip/shared/jobs/`), a sam żadnych workflowów
nie uruchamia.

- `POST /planning/jobs` zleca wygenerowanie planu i zwraca `workflow_id`.
  Stan, wynik, błąd i postęp zwraca `GET /jobs/{id}`, a `POST /jobs/{id}/cancel` anuluje zadanie.
- Kontrakt (nazwy workflowów i kolejek, payloady, `CONTRACT_VERSION`) jest
  w repozytorium workera. Tutaj trzymamy jego kopię w `shared/jobs/contracts.py`
  i wygenerowany plik `contracts/jobs.schema.json`. Job CI `contracts-check`
  porównuje ten plik z wersją workera.
- Schemat bazy i migracje należą do backendu. Worker łączy się rolą
  `tuttitrip_worker` bez prawa do DDL i zapisuje tylko tabele wymienione w
  `deploy/worker-grants.sql`.
- Worker co około 30 s zapisuje heartbeat, a `/health` pokazuje
  `worker: ok | stale | missing`. Gdy workera brakuje, endpointy zlecające
  zadania zwracają 503 z czytelnym komunikatem.
- Po każdym wdrożeniu pipeline zleca workflow `ping` i czeka na jego wynik.

Lokalnie backend i worker korzystają z tej samej bazy z Docker Compose. Po
krokach z sekcji „Baza danych i migracje” uruchom workera z jego repozytorium
z `DBOS_SYSTEM_DATABASE_URL=postgresql://tuttitrip:tuttitrip@localhost:5432/tuttitrip`
i `DBOS__APPVERSION=local`, a potem sprawdź połączenie:
`curl -X POST localhost:8000/jobs/ping`. Wszystkie zasady współpracy
(nazwy, pliki env, wersje, sprzątanie) opisuje
[deploy/CONVENTIONS.md](deploy/CONVENTIONS.md).

## Architektura

Kod jest podzielony na pionowe moduły domenowe (vertical slices). Każda domena
i poddomena ma taki sam układ plików:

| Plik | Zawartość |
| --- | --- |
| `api.py` | router FastAPI (jedyne miejsce, w którym wolno importować `fastapi`) |
| `schemas.py` | modele Pydantic: żądania, odpowiedzi, DTO |
| `services/` | po jednym module na serwis; tu żyją też agenci Pydantic AI |
| `models.py` + `db.py` | modele SQLAlchemy i zapytania (tylko gdy domena coś zapisuje) |
| `logic/` | opcjonalna czysta logika: solver, reguły, obliczenia |

```text
src/tuttitrip/
├── main.py            # fabryka aplikacji, rejestracja routerów
├── shared/            # wspólne komponenty, nie znają domen
│   ├── config/        # Settings
│   ├── db/            # Base, silnik, sesje
│   ├── auth/          # weryfikacja tokenów Auth0, GET /me
│   ├── health/        # GET /health, GET /health/live
│   └── jobs/          # klient DBOS: zlecanie zadań workerowi, kontrakt
├── trips/             # wyjazdy (wzorcowa domena: api -> services -> db)
├── profiles/          # uczestnicy wyjazdu (wagi, grupy wiekowe)
├── interview/         # wywiad prowadzony przez AI
├── planning/          # agent planujący
│   ├── fairness/      # solver sprawiedliwości (czysta logika)
│   └── linter/        # linter planu (czysta logika)
├── accommodation/     # wymagania wobec noclegu
├── expenses/          # wydatki
│   └── settlement/    # rozliczenie sald (czysta logika)
└── search/            # embeddingi w pgvector (zapisuje je worker)
```

Zasady sprawdzane przez `tests/architecture/` (pytest-archon i testy struktury):

- każda domena ma wymagane pliki, a `db.py` istnieje wtedy i tylko wtedy, gdy jest `models.py`;
- `shared` nie importuje żadnej domeny;
- domena korzysta z innej domeny tylko przez jej `services` albo `schemas`;
- `fastapi` importuje tylko `api.py`, `pydantic_ai` tylko `services`,
  a `sqlalchemy` tylko `models.py`, `db.py`, `services` i `shared.db`;
- moduły w `logic/` i pliki `schemas.py` nie sięgają (nawet pośrednio) po
  FastAPI, Pydantic AI, SQLAlchemy ani bazę;
- pliki `__init__.py` niczego nie importują, bo pytest-archon nie widzi
  importów wykonywanych przez pakiety nadrzędne.

Szczegóły i konwencje dla zespołu i agentów są w [AGENTS.md](AGENTS.md).

## Wdrożenie

Każdy push uruchamia CI (`checks`: ruff, ty, pytest) na runnerach organizacji
`[self-hosted, hackathon]`. Jeśli CI przejdzie, job `deploy` buduje obraz Dockera
i wdraża go na serwer `dellpromaxgb10`, na którym działa osobny runner
`tuttitrip-deploy`.

| Gałąź | Adres | Baza danych |
| --- | --- | --- |
| `main` | https://tuttitrip-api.gburek.app | `tuttitrip_main` (trwała) |
| `develop` | https://tuttitrip-api-develop.gburek.app | `tuttitrip_develop` (trwała) |
| każda inna | `https://tuttitrip-api-<slug>.gburek.app` | `tuttitrip_br_<slug>` (usuwana razem z gałęzią) |

Slug powstaje z nazwy gałęzi: małe litery, każdy ciąg znaków innych niż litery
i cyfry zamieniony na `-`, a cała etykieta ma najwyżej 63 znaki. Przykład:
`feature/cos tam` daje `tuttitrip-api-feature-cos-tam.gburek.app`.

Jak to działa na serwerze:

- jedna baza `tuttitrip-postgres` (PostgreSQL 18 + pgvector), osobna baza danych dla każdej gałęzi;
- obok API działa kontener workera `tuttitrip-worker-<env>`. Jeśli worker nie ma
  jeszcze obrazu dla danej gałęzi, wdrożenie uruchamia go z obrazu `develop` albo `main`;
- przed startem nowego kontenera migracje wykonuje jednorazowy kontener (`alembic upgrade head`);
- `tuttitrip-gateway` (nginx) kieruje ruch do kontenera gałęzi według nagłówka `Host`;
- skrypt dodaje regułę ruchu (ingress) dla gałęzi do współdzielonego Cloudflare
  Tunnel przez API i przed każdą zmianą zapisuje kopię konfiguracji w `~/tuttitrip/backups/`;
- przy każdym wdrożeniu i po usunięciu gałęzi skrypt usuwa kontenery, obrazy, bazy
  i reguły gałęzi, których już nie ma na GitHubie; `main` i `develop` pomija zawsze.

Skrypty są w `deploy/`. Sekrety trzymamy w GitHub Actions secrets/variables
i w plikach `~/tuttitrip/*.env` na serwerze, nigdy w repozytorium.

## Git flow

- `main` to produkcja, `develop` to gałąź integracyjna. Obie są chronione:
  zmiany wchodzą tylko przez PR z zielonym CI, bez force-pusha i bez usuwania.
- Nową pracę zaczynasz od `develop` na gałęzi `feature/<nazwa>`, `fix/<nazwa>`
  albo `chore/<nazwa>` i otwierasz PR do `develop`. Każda gałąź dostaje własny
  podgląd pod `https://tuttitrip-api-<slug>.gburek.app`.
- Wydanie to PR z `develop` do `main`.
- Gałęzie po scaleniu usuwają się same, a ich wdrożenie znika razem z nimi.
