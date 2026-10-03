# TuttiTrip – backend

Backend aplikacji **TuttiTrip**, tworzonej przez nasz zespół podczas hackathonu **HackYeah**.
Repozytorium zawiera logikę serwerową oraz agentów AI opartych o
[Pydantic AI](https://ai.pydantic.dev/), którzy pomagają planować podróże.

Frontend aplikacji znajduje się w osobnym repozytorium (`tuttitrip-frontend`).

## Stack

| Obszar                         | Narzędzie                                                      |
| ------------------------------ | -------------------------------------------------------------- |
| Język                          | Python 3.14                                                    |
| Zarządzanie projektem/zależn.  | [uv](https://docs.astral.sh/uv/)                               |
| Modele danych / walidacja      | [Pydantic](https://docs.pydantic.dev/)                         |
| Agenci AI                      | [Pydantic AI](https://ai.pydantic.dev/)                        |
| Linter i formatter             | [Ruff](https://docs.astral.sh/ruff/) (`select = ["ALL"]`)      |
| Sprawdzanie typów              | [ty](https://docs.astral.sh/ty/) (wszystkie reguły jako błędy) |
| Testy                          | [pytest](https://docs.pytest.org/)                             |
| Testy architektury             | [pytest-archon](https://github.com/jwbargsten/pytest-archon)   |

## Wymagania

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (w wersji 0.12 lub nowszej)
- Python 3.14 – jeśli nie masz go lokalnie, uv może go zainstalować:

  ```bash
  uv python install 3.14
  ```

- Klucz API wybranego dostawcy modeli (np. `OPENAI_API_KEY`) – potrzebny tylko do
  faktycznego uruchamiania agentów. Testy nie wykonują żadnych zapytań do modeli.

## Uruchomienie

Instalacja zależności (tworzy środowisko `.venv`, razem z grupą `dev`):

```bash
uv sync
```

Testy:

```bash
uv run pytest
```

Linter i formatowanie:

```bash
uv run ruff check .          # lint
uv run ruff check --fix .    # lint z automatycznymi poprawkami
uv run ruff format .         # formatowanie
uv run ruff format --check . # sprawdzenie formatowania (np. w CI)
```

Sprawdzanie typów:

```bash
uv run ty check
```

Wszystko naraz (przed commitem):

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
```

Dodawanie zależności:

```bash
uv add <pakiet>              # zależność runtime
uv add --dev <pakiet>        # zależność deweloperska (grupa `dev`)
```

## Struktura katalogów

```text
tuttitrip-backend/
├── pyproject.toml           # metadane projektu, zależności, konfiguracja ruff/ty/pytest
├── uv.lock                  # zablokowane wersje zależności
├── .python-version          # wersja Pythona (3.14)
├── src/
│   └── tuttitrip/
│       ├── __init__.py
│       ├── domain/          # czyste modele domenowe (Pydantic), bez zależności od AI
│       │   └── trip.py
│       └── agents/          # agenci Pydantic AI korzystający z modeli domenowych
│           └── planner.py
└── tests/
    ├── conftest.py              # blokada prawdziwych zapytań do modeli w testach
    ├── test_architecture.py     # reguły warstw (pytest-archon)
    └── test_planner_agent.py    # test agenta z użyciem TestModel
```

## Zasady architektury

Warstwa `tuttitrip.domain` musi pozostać niezależna: nie może importować
`tuttitrip.agents` ani `pydantic_ai`. Reguła jest sprawdzana automatycznie w
`tests/test_architecture.py` za pomocą pytest-archon, więc złamanie jej kończy się
nieudanym testem.

Agentów testujemy bez sieci, podmieniając model na `TestModel` / `FunctionModel`
z Pydantic AI (`agent.override(model=...)`). W `tests/conftest.py` ustawione jest
`models.ALLOW_MODEL_REQUESTS = False`, które blokuje przypadkowe zapytania do
prawdziwych dostawców.
