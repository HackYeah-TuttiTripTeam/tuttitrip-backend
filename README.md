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
klienta TypeScript z `/api/v1/openapi.json` tego API.

![Plansza „Kod liczy, model tylko pisze”: po lewej solver sprawiedliwości, sprawdzenie planu, kontrakt noclegu i rozliczenie, po prawej to, co robi model językowy: pytania, rozumienie odpowiedzi, uzasadnienia i odczyt wklejonych planów.](docs/readme/09-code.webp)

> [!NOTE]
> Opis całego projektu, plansze, zrzuty aplikacji i instrukcja uruchomienia wszystkich części są w repozytorium zbiorczym [tuttitrip](https://github.com/HackYeah-TuttiTripTeam/tuttitrip). TuttiTrip powstał z pomocą asystentów kodowania (Claude Code, Codex). Ludzie z zespołu odpowiadali za architekturę rozwiązania, rozplanowanie funkcji, działanie aplikacji i to, jak się z niej korzysta.

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

### Dane miast pokazowych

Warszawa (demo), Gdańsk, Kraków i Berlin pochodzą z publicznego arkusza Google
(`TUTTITRIP_CITIES__SHEET_ID`). Na serwerze pobiera go `deploy/fetch-cities.sh`
po każdym wdrożeniu i importuje do bazy; lokalnie zrobisz to ręcznie:

```bash
mkdir -p data/cities    # katalog jest w .gitignore
curl -fL "https://docs.google.com/spreadsheets/d/$TUTTITRIP_CITIES__SHEET_ID/export?format=xlsx" \
  -o data/cities/miasta.xlsx
TUTTITRIP_CITIES__DATA_DIR=data/cities uv run python -m tuttitrip.places.services.import_command
# samo sprawdzenie pliku bez bazy:  ... import_command data/cities/miasta.xlsx --check
```

Import jest idempotentny (upsert po `source_key`) i działa w jednej transakcji:
błędny arkusz kończy się kodem 1 z listą wierszy i niczego nie zmienia.

### 3. API

```bash
uv run uvicorn tuttitrip.main:app --reload
```

- http://localhost:8000/api/v1/docs: dokumentacja Swagger
- http://localhost:8000/api/v1/openapi.json: schemat dla generatora klienta TS
- http://localhost:8000/api/v1/health: stan aplikacji, bazy i workera (`503`, gdy baza nie odpowiada albo worker ma niezgodną wersję kontraktu)
- http://localhost:8000/api/v1/me: dane zalogowanego użytkownika i jego uprawnienia (wymaga tokenu Auth0)

Wszystkie endpointy są pod `/api/v1/` (stała `API_PREFIX` w `main.py`,
pilnuje tego test `tests/architecture/test_routes.py`). Wdrożone frontendy
wołają API przez własny proxy `/api/*`, czyli z tej samej domeny.

Całość w kontenerach (baza, jednorazowe migracje, API):

```bash
docker compose up --build
```

### 4. Testy, lint i typy

```bash
uv run pytest -m "not integration and not e2e"  # testy jednostkowe i architektury, to samo robi CI
uv run pytest -m integration            # lokalnie przed PR; dziś brak takich testów, więc kod wyjścia 5 ("nic nie wybrano") to nie błąd
uv run pytest                                    # wszystko
uv run ruff check .           # lint (uv run ruff check --fix . poprawia, co się da)
uv run ruff format .          # formatowanie
uv run ty check               # typy
```

Przed każdym commitem (CI sprawdza to samo):

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest -m "not integration and not e2e"
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

- `POST /api/v1/planning/jobs` zleca wygenerowanie planu i zwraca `workflow_id`.
  Stan, wynik, błąd i postęp zwraca `GET /api/v1/jobs/{id}`, a `POST /api/v1/jobs/{id}/cancel` anuluje zadanie.
- `POST /api/v1/trips/{trip_id}/plans` i `GET /api/v1/trips/{trip_id}/plans/latest` zwracają plan z miarą
  sprawiedliwości (`docs/algorytm.md`, sekcja 10). Na razie stała odpowiedź (stub) do czasu solvera.
- Kontrakt (nazwy workflowów i kolejek, payloady, `CONTRACT_VERSION`) jest
  w repozytorium workera. Tutaj trzymamy jego kopię w `shared/jobs/contracts.py`
  i wygenerowany plik `contracts/jobs.schema.json`. Job CI `contracts-check`
  porównuje ten plik z wersją workera.
- Schemat bazy i migracje należą do backendu. Worker łączy się rolą
  `tuttitrip_worker` bez prawa do DDL i zapisuje tylko tabele wymienione w
  `deploy/worker-grants.sql`.
- Worker co około 30 s zapisuje heartbeat, a `/api/v1/health` pokazuje
  `worker: ok | stale | missing`. Gdy workera brakuje, endpointy zlecające
  zadania zwracają 503 z czytelnym komunikatem.
- Po każdym wdrożeniu pipeline zleca workflow `ping` i czeka na jego wynik.

Lokalnie backend i worker korzystają z tej samej bazy z Docker Compose. Po
krokach z sekcji „Baza danych i migracje” uruchom workera z jego repozytorium
z `DBOS_SYSTEM_DATABASE_URL=postgresql://tuttitrip:tuttitrip@localhost:5432/tuttitrip`
i `DBOS__APPVERSION=local`, a potem sprawdź połączenie:
`curl -X POST localhost:8000/api/v1/jobs/ping`. Wszystkie zasady współpracy
(nazwy, pliki env, wersje, sprzątanie) opisuje
[deploy/CONVENTIONS.md](deploy/CONVENTIONS.md).

## Uprawnienia

Każdy endpoint wymaga uprawnienia `READ` albo `WRITE` do funkcjonalności z
drzewa w `shared/permissions/registry.py` (np. `trips.core`, `trips.members`).
`WRITE` obejmuje `READ`, a uprawnienie na grupie (`trips`) obejmuje wszystko
pod nią. Wyjątki bez logowania to `/api/v1/health`, smoke test
`/api/v1/jobs/ping` i dokumentacja (`/api/v1/docs`, `/api/v1/openapi.json`).
Dalej ścieżki podajemy bez prefiksu `/api/v1`.

- Rola `user` ma każdy zalogowany. Superadmini (claim Auth0 `admin`) mają
  `*:WRITE`, czyli wszystko. Pozostałe role i uprawnienia nadaje się przez
  `/admin/permissions/...`, a każda zmiana trafia do dziennika.
- `GET /api/v1/me` zwraca `access`, płaską mapę funkcjonalność → poziom (np.
  `{"trips.core": "WRITE", "search": "READ"}`). Frontend ukrywa na jej
  podstawie niedostępne elementy.
- Uprawnienia do funkcjonalności są globalne. To, czy możesz zmienić *ten*
  wyjazd, zależy od twojej roli na nim (`host`, `co_host`, `member`;
  `my_role` w `TripRead`). Spoza wyjazdu dostajesz 404.
- Wymagane uprawnienie widać w `/api/v1/docs` i w `x-required-permission` w schemacie.

Szczegóły (dodawanie funkcjonalności, ochrona endpointu, role) są w
[AGENTS.md](AGENTS.md#uprawnienia).

## Serwer MCP

Backend wystawia serwer MCP (Streamable HTTP, tryb bezstanowy, odpowiedzi JSON)
pod `/api/v1/mcp`, żeby użytkownik mógł podłączyć swoje dane do Claude,
ChatGPT albo innego klienta MCP. Kod jest w domenie `src/tuttitrip/mcp/`
(FastMCP, `fastmcp-slim[server]`), montaż w `create_app()` tylko przy
`TUTTITRIP_MCP__ENABLED=true` (deploy włącza go w main i develop, na gałęziach podglądu wyłącza).

- Token to token dostępowy Auth0 z audience równym adresowi serwera
  (`TUTTITRIP_MCP__RESOURCE_URL`, np. `https://tuttitrip-api.gburek.app/api/v1/mcp`),
  czyli innym niż audience API aplikacji. Token z audience API aplikacji daje 401.
  Podpis, wystawca i ważność sprawdza ten sam `TokenVerifier` co w REST.
- Endpoint to jedna dokładna trasa `/api/v1/mcp` (bez montowania, więc REST zachowuje 405 i przekierowania). Metadane zasobu (RFC 9728) są pod `/.well-known/oauth-protected-resource/api/v1/mcp`
  na poziomie głównym domeny; 401 niesie `WWW-Authenticate` z `resource_metadata`.
- Uprawnienia: każde narzędzie ma dokładnie jeden `mcp_requires(Feature.X, Access.Y)`,
  który wymaga `mcp:READ` i `X:Y`. Narzędzie bez zgody znika z `tools/list`.
  Dostęp do konkretnego wyjazdu dalej zależy od roli na nim.
- Narzędzia: `whoami`, `list_trips` (stronicowane), `get_trip`.

### Podłączenie w Claude, ChatGPT i Claude Code

Adres serwera zależy od środowiska. Używaj dokładnie tych adresów (bez ukośnika
na końcu); serwer jest tylko na hoście API, więc `tuttitrip.gburek.app/api/v1/mcp`
nie zadziała.

| Środowisko | Adres MCP |
| --- | --- |
| produkcja (`main`) | `https://tuttitrip-api.gburek.app/api/v1/mcp` |
| develop (do prób) | `https://tuttitrip-api-develop.gburek.app/api/v1/mcp` |
| gałęzie podglądu | brak: `/api/v1/mcp` odpowiada 404 (`TUTTITRIP_MCP__ENABLED=false`) |

Potrzebujesz konta TuttiTrip (Google, Discord albo e-mail i hasło) i planu
klienta, który pozwala na własny serwer MCP:

| Klient | Plan |
| --- | --- |
| Claude (claude.ai, aplikacja desktopowa) | Free (jeden własny konektor), Pro, Max, Team i Enterprise (na Team i Enterprise konektor dodaje właściciel organizacji, a członkowie łączą się własnym kontem) |
| ChatGPT | Developer mode na Pro, Plus, Business, Enterprise i Education, tylko w przeglądarce |
| Claude Code | każdy, wystarczy zalogowany `claude` |

**Claude** ([dokumentacja](https://claude.com/docs/connectors/custom/remote-mcp)):

1. **Customize > Connectors > Add custom connector**.
2. W polu adresu serwera wpisz adres z tabeli. Uwierzytelnianie: „Sign in now”.
3. „OAuth client”: „Use Claude's published identity” (CIMD, zalecane), a jeśli
   tenant go jeszcze nie obsługuje, „Register automatically” (DCR). Własnego
   klienta OAuth nie wpisujesz.
4. **Add**, potem **Connect**: zalogujesz się w Auth0 (Google, Discord albo
   hasłem) i zaakceptujesz dostęp.
5. W rozmowie włącz konektor przyciskiem **+ > Connectors**. Poproś: „Kim jestem
   w TuttiTrip?”, powinien zadziałać `whoami`.

Ustawień uwierzytelniania nie da się zmienić po dodaniu konektora; po błędzie
albo po zmianie adresu usuń konektor i dodaj go od nowa.

**ChatGPT** ([dokumentacja](https://developers.openai.com/api/docs/guides/developer-mode)):

1. **Settings > Security and login**: włącz „Developer mode”.
2. W ChatGPT otwórz **Plugins**, plus i utwórz aplikację developer mode z adresem
   MCP. Transport: streaming HTTP. Uwierzytelnianie: OAuth (CIMD albo DCR).
3. Aplikacja trafia do „Drafts”; zaloguj się w Auth0 przy pierwszym użyciu.
   Narzędzia TuttiTrip są tylko do odczytu, więc ChatGPT nie pyta o potwierdzenie
   zapisu.

**Claude Code** ([dokumentacja](https://code.claude.com/docs/en/mcp)):

```bash
claude mcp add --transport http tuttitrip https://tuttitrip-api.gburek.app/api/v1/mcp
# lub do prób: https://tuttitrip-api-develop.gburek.app/api/v1/mcp
claude            # w sesji: /mcp, wybierz „tuttitrip” i zaloguj się w przeglądarce
claude mcp list   # status serwera; usunięcie: claude mcp remove tuttitrip
```

Domyślny zakres to `local` (tylko bieżący projekt); `--scope user` udostępnia
serwer we wszystkich projektach. Po zmianie adresu usuń serwer i dodaj go ponownie.

**Co widać po zalogowaniu.** Narzędzia tylko do odczytu, zawsze w granicach
twojego konta: `whoami` (twój identyfikator `sub`, role, czy jesteś adminem i mapa uprawnień), `list_trips`
(twoje wyjazdy z rolą na każdym) i `get_trip` (jeden wyjazd, którego jesteś
uczestnikiem; cudzy daje „Trip not found”). Narzędzie znika z listy, gdy twoje
konto nie ma uprawnienia `mcp` albo `trips`.

**Gdy coś nie działa:**

- 401 bez logowania jest normalny: odpowiedź niesie `WWW-Authenticate` z adresem
  metadanych, od których klient zaczyna. Sprawdzisz to bez klienta:
  `deploy/smoke-mcp.sh https://tuttitrip-api-develop.gburek.app` (401, metadane
  zasobu, serwer autoryzacji).
- 401 po zalogowaniu: token ma zły `aud`. Musi być równy adresowi MCP środowiska,
  a nie `https://tuttitrip-api.gburek.app` (audience API aplikacji).
- „Client ... is not authorized to access resource server”: klient nie jest
  dopuszczony do API MCP w Auth0 (krok 1 poniżej).
- Logowanie kończy się błędem po wyborze Google lub Discord: połączenia nie są
  na poziomie domeny (krok 4 poniżej).
- Błąd rejestracji klienta: DCR albo CIMD nie są włączone w tenancie (kroki 2 i 3).
- Autoryzacja w kliencie nie przechodzi przez gałąź podglądu: tam MCP jest wyłączone
  celowo, bo Auth0 ma API tylko dla main i develop.

**Stan tenantu Auth0 (odczyt 2026-10-04, nic nie zmieniano).** Publiczne
`/.well-known/openid-configuration` ma `registration_endpoint` (DCR) i
`authorization_response_iss_parameter_supported: true`, ale nie ma jeszcze
znacznika obsługi CIMD, więc w Claude wybierz „Register automatically”. API
dla adresu MCP develop istnieje (Auth0 odmawia aplikacji frontendu dostępu do niego,
czyli zasób jest zarejestrowany). Co jeszcze musi zrobić właściciel tenantu, a
blokuje pełne przejście Claude i ChatGPT: włączyć CIMD (krok 2 poniżej), upewnić
się, że DCR jest włączony (krok 3) i przenieść połączenia na poziom domeny (krok 4).
Dopóki tego nie ma, serwer działa, ale zalogowanie w kliencie MCP może się nie udać;
kryteria akceptacji #104 (Claude, ChatGPT, Claude Code) potwierdza się dopiero po tym.

### Konfiguracja Auth0 pod MCP (chore #102)

Issue #102 zostaje otwarte, dopóki właściciel nie zweryfikuje tenantu według kryteriów akceptacji. Tenant `dev-yahwm2zlut2gqdry.us.auth0.com` konfiguruje właściciel tenantu w
panelu (agent i kod tego nie zmieniają). Bez tego Claude i ChatGPT zatrzymają
się na logowaniu. Kroki, kolejno:

1. **API (Resource Servers)**, po jednym na środowisko, podpis RS256.
   Identyfikator musi być znak w znak równy `TUTTITRIP_MCP__RESOURCE_URL`
   (bez ukośnika na końcu):
   - `https://tuttitrip-api.gburek.app/api/v1/mcp` (main),
   - `https://tuttitrip-api-develop.gburek.app/api/v1/mcp` (develop).

   ```bash
   auth0 apis create --name "TuttiTrip MCP" \
     --identifier https://tuttitrip-api.gburek.app/api/v1/mcp --signing-alg RS256
   auth0 apis create --name "TuttiTrip MCP (develop)" \
     --identifier https://tuttitrip-api-develop.gburek.app/api/v1/mcp --signing-alg RS256
   ```
2. **Settings > Advanced > Settings** (włączniki):
   - „Resource Parameter Compatibility Profile”: Auth0 bierze `resource`, gdy
     nie ma `audience` (przy obu wygrywa `audience`),
   - „Include Issuer in Authorization Responses”: parametr `iss` w odpowiedzi
     autoryzacji (obrona przed mix-up),
   - „Client ID Metadata Document Registration” (CIMD): klienci rejestrują się
     adresem URL metadanych, jako aplikacje third-party w trybie `strict`.
3. **Dynamic Client Registration** dla klientów bez CIMD:
   `auth0 api patch tenants/settings --data '{"flags":{"enable_dynamic_client_registration":true}}'`.
   To otwarta rejestracja (każdy może założyć aplikację bez tokenu); ryzyko
   i środki zaradcze: „Securing an Open DCR Endpoint” w dokumentacji Auth0.
4. **Połączenia na poziomie domeny**: Google (`google-oauth2`), Discord i baza
   haseł (strategia `auth0`) przenieś na poziom domeny („Promote connection to
   domain level”), bo aplikacje CIMD i DCR są third-party i widzą tylko takie:
   `auth0 api get connections` (skopiuj `id`), potem dla każdego
   `auth0 api patch connections/<id> --data '{"is_domain_connection":true}'`.
5. **Akcja „TuttiTrip superadmins”** nie wymaga zmian: odrzuca tylko aplikację
   bramki admina (`ADMIN_GATE_CLIENT_IDS`), a claim `.../roles` dodaje każdemu
   tokenowi dostępowemu osoby z listy superadminów, także dla klienta MCP.
   Konto spoza listy nie dostaje claimu `admin`.

Weryfikacja: `/.well-known/openid-configuration` tenantu ma `registration_endpoint`
i znacznik obsługi CIMD (`client_id_metadata_document_supported`, nazwę pola
potwierdzić w odpowiedzi tenantu); MCP Inspector z logowaniem Google daje token
z `aud` równym adresowi MCP danego środowiska.

Stan na dziś (odczyt publicznego `/.well-known/openid-configuration`): jest już
`registration_endpoint` (`/oidc/register`) i `authorization_response_iss_parameter_supported: true`,
nie ma jeszcze znacznika CIMD.

Gałęzie podglądu nie mają serwera MCP: deploy zapisuje im
`TUTTITRIP_MCP__ENABLED=false` (`tt_mcp_env` w `deploy/lib.sh`), więc `/api/v1/mcp`
i metadane zasobu odpowiadają 404. Auth0 ma API tylko dla main i develop, bo
identyfikator API jest adresem serwera.

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
│   ├── auth/          # weryfikacja tokenów Auth0 (CurrentUser)
│   ├── permissions/   # uprawnienia READ/WRITE, role, GET /api/v1/me, API admina
│   ├── health/        # GET /api/v1/health, GET /api/v1/health/live
│   └── jobs/          # klient DBOS: zlecanie zadań workerowi, kontrakt
├── accounts/          # własne konto Auth0: zmiana nazwy (PATCH /api/v1/me/account)
├── trips/             # wyjazdy (wzorcowa domena: api -> services -> db)
├── profiles/          # uczestnicy wyjazdu (wagi, grupy wiekowe)
├── interview/         # wywiad prowadzony przez AI
├── planning/          # agent planujący
│   ├── fairness/      # solver sprawiedliwości (czysta logika)
│   ├── plans/         # plan z miarą, księgą i werdyktami (na razie stała odpowiedź)
│   └── linter/        # linter planu (czysta logika)
├── accommodation/     # wymagania wobec noclegu
├── expenses/          # wydatki
│   └── settlement/    # rozliczenie sald (czysta logika)
└── search/            # embeddingi w pgvector (zapisuje je worker)
```

![Diagram architektury w czterech kolumnach: ludzie, aplikacja, backend oraz worker i modele, połączone kropkowanymi liniami, pod nim lista technologii.](docs/readme/14-stack.webp)

Co liczy czysty kod z `planning/` i `expenses/` (plansze na danych przykładowych):

<table>
  <tr>
    <td width="33%"><img width="100%" src="docs/readme/04-fairness.webp" alt="Wykres zadowolenia pięciu osób w skali 0–100 z podłogą 40 punktów. W planie TuttiTrip najniższy wynik ma Kuba, 58; w planie z czatbota babcia miała 22."><br><sub>Solver sprawiedliwości: najmniej zadowolona osoba ma 58 punktów zamiast 22.</sub></td>
    <td width="33%"><img width="100%" src="docs/readme/02-problem.webp" alt="Plan poniedziałku z czatbota z trzema problemami: muzeum zamknięte w poniedziałek, 9 km pieszo z babcią, budżet przekroczony o 240 zł. Obok wynik sprawdzenia: 3 problemy kontra 0 w planie TuttiTrip."><br><sub>Linter planu: 3 problemy w planie z czatbota, 0 w planie TuttiTrip.</sub></td>
    <td width="33%"><img width="100%" src="docs/readme/05-decision.webp" alt="Telefon z propozycją przeniesienia Westerplatte na sobotę i kartą „Czeka na Twoją decyzję”: sprawiedliwość 0,87 na 0,71, Kuba 58 na 34, budżet plus 80 zł."><br><sub>Koszt decyzji organizatora: sprawiedliwość, najmniej zadowolona osoba, budżet.</sub></td>
  </tr>
</table>

Algorytm planu i miary sprawiedliwości (równania E0 do E6, parametry, testy) jest opisany w
[docs/algorytm.md](docs/algorytm.md). To kanoniczna specyfikacja dla `planning/**/logic`.

Zasady sprawdzane przez `tests/architecture/` (pytest-archon i testy struktury):

- każda domena ma wymagane pliki, a `db.py` istnieje wtedy i tylko wtedy, gdy jest `models.py`;
- `shared` nie importuje żadnej domeny;
- domena korzysta z innej domeny tylko przez jej `services` albo `schemas`;
- `fastapi` importuje tylko `api.py`, `pydantic_ai` tylko `services`,
  a `sqlalchemy` tylko `models.py`, `db.py`, `services` i `shared.db`;
- moduły w `logic/` i pliki `schemas.py` nie sięgają (nawet pośrednio) po
  FastAPI, Pydantic AI, SQLAlchemy ani bazę;
- pliki `__init__.py` niczego nie importują, bo pytest-archon nie widzi
  importów wykonywanych przez pakiety nadrzędne;
- każdy endpoint deklaruje uprawnienie (`requires`) albo jest jawnie
  publiczny (`public()`), a trasy z `{trip_id}` sprawdzają członkostwo w wyjeździe.

Szczegóły i konwencje dla zespołu i agentów są w [AGENTS.md](AGENTS.md).

## Wdrożenie

Każdy push uruchamia CI raz: `lint` (ruff, ty) i `tests` (pytest) równolegle na
runnerach organizacji `[self-hosted, hackathon]`, `contracts-check` też na tych runnerach. Job `deploy` buduje obraz Dockera i wdraża go na serwer
`dellpromaxgb10`, na którym działa osobny runner `tuttitrip-deploy`. Podgląd
gałęzi wdraża się od razu, `main` i `develop` czekają na zielone `lint` i
`tests`. Nowy push do tej samej gałęzi anuluje niedokończone testy poprzedniego.

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
- arkusz miast (`deploy/fetch-cities.sh`) trafia do wolumenu `tuttitrip-cities-data` (wspólny dla
  środowisk, `cleanup.sh` go nie usuwa) i zaraz po migracjach importuje go jednorazowy kontener.
  Błąd pobierania albo importu to `WARNING` w logu, a nie porażka wdrożenia: zostaje ostatnia
  poprawna kopia i dane w bazie. Brak danych Warszawy po wdrożeniu zgłasza smoke test;
- `tuttitrip-gateway` (nginx) kieruje ruch do kontenera gałęzi według nagłówka `Host`;
- skrypt dodaje regułę ruchu (ingress) dla gałęzi do współdzielonego Cloudflare
  Tunnel przez API i przed każdą zmianą zapisuje kopię konfiguracji w `~/tuttitrip/backups/`;
- przy każdym wdrożeniu i po usunięciu gałęzi skrypt usuwa kontenery, obrazy, bazy
  i reguły gałęzi, których już nie ma na GitHubie; `main` i `develop` pomija zawsze.

Skrypty są w `deploy/`. Sekrety trzymamy w GitHub Actions secrets/variables
i w plikach `~/tuttitrip/*.env` na serwerze, nigdy w repozytorium.

## Git flow

- `main` to produkcja, `develop` to gałąź integracyjna. Zmiany wchodzą do nich
  tylko przez PR z zielonymi jobami `lint`, `tests` i `contracts-check`, bez
  force-pusha i bez usuwania gałęzi. Nie wymagamy zatwierdzeń (0 approvals),
  bo w 5 osób na 24 godziny bramką jest CI.
- GitHub nie pozwala wymusić tych reguł w prywatnym repozytorium organizacji
  na darmowym planie (ochrona gałęzi i rulesety zwracają 403), więc na razie
  pilnujemy ich sami. Po przejściu na płatny plan można je włączyć jednym
  wywołaniem:

  ```bash
  for b in main develop; do
    gh api -X PUT repos/HackYeah-TuttiTripTeam/tuttitrip-backend/branches/$b/protection --input - <<'JSON'
  {"required_status_checks": {"strict": false, "contexts": ["checks", "contracts-check"]},
   "enforce_admins": true,
   "required_pull_request_reviews": {"required_approving_review_count": 0},
   "restrictions": null, "allow_force_pushes": false, "allow_deletions": false}
  JSON
  done
  ```
- Nową pracę zaczynasz od `develop` na gałęzi `feature/<nazwa>`, `fix/<nazwa>`
  albo `chore/<nazwa>` i otwierasz PR do `develop`. Każda gałąź dostaje własny
  podgląd pod `https://tuttitrip-api-<slug>.gburek.app`.
- Wydanie to PR z `develop` do `main`.
- Tytuły zgłoszeń zaczynają się od `feat:`, `docs:`, `chore:` albo `bug:`,
  a tytuły PR od `feat:`, `docs:`, `chore:` albo `bugfix:` (wydanie:
  `release:`). Opisy piszemy po polsku według formularza i szablonu PR. PR do
  `develop` mergujemy przez "Squash and merge", wydanie przez "Create a merge
  commit". Notatki wydań tworzy Release Drafter w GitHub Releases. Pełne
  zasady: [CONTRIBUTING.md](https://github.com/HackYeah-TuttiTripTeam/.github/blob/main/CONTRIBUTING.md).
- Po scaleniu PR gałąź usuwa workflow `Delete merged branch` i uruchamia
  sprzątanie, więc jej wdrożenie znika razem z nią. `main` i `develop` nie są
  nigdy usuwane, dlatego PR wydania idzie prosto z `develop`. Automatyczne
  usuwanie gałęzi w ustawieniach GitHuba jest wyłączone, bo bez ochrony gałęzi
  skasowałoby też `develop` po scaleniu PR wydania (`develop` -> `main`).
