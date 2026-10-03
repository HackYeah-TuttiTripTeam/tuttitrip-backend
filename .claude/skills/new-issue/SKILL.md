---
name: new-issue
description: Przygotowuje zgłoszenie (issue) dla repozytoriów TuttiTrip w wymaganym formacie, czyli z prefiksem typu w tytule i kompletem sekcji po polsku, i zakłada je przez gh. Użyj, gdy trzeba zgłosić błąd albo zaplanować funkcję, dokumentację lub prace techniczne.
---

# Nowe zgłoszenie

Zasady są wspólne dla całej organizacji i opisane w
https://github.com/HackYeah-TuttiTripTeam/.github/blob/main/CONTRIBUTING.md.
Workflow `Issue format` zamyka zgłoszenia, które ich nie spełniają, więc
szkic sprawdź przed wysłaniem.

1. Wybierz typ:
   - `feat`: nowa funkcja albo zmiana zachowania,
   - `bug`: coś działa inaczej, niż powinno,
   - `docs`: README, AGENTS.md, opisy API,
   - `chore`: konfiguracja, CI, zależności, porządki.
2. Napisz tytuł `typ: opis` albo `typ(zakres): opis`. Zakres to małe litery,
   cyfry i myślniki, np. `frontend`, `backend`, `worker`, `infra`. Opis ma
   co najmniej 4 znaki. Tytuł musi pasować do
   `^(feat|docs|chore|bug)(\([a-z0-9-]+\))?: \S.{3,}`.
3. Zbierz treść sekcji. Jeśli czegoś nie wiesz (np. kroków do odtworzenia
   błędu albo powodu zmiany), zapytaj użytkownika. Nie zgaduj i nie zostawiaj
   placeholderów: `...`, `TODO`, `<tekst>`, `_No response_` i samo `- [ ]`
   bot traktuje jak pustą sekcję.
4. Zapisz treść do pliku tymczasowego. Szablon dla `feat` (dla `docs` i
   `chore` bez sekcji Poza zakresem i z krótszą listą DoD):

   ```markdown
   ### Opis
   Co ma powstać, z perspektywy użytkownika.

   ### Dlaczego
   Jaki problem rozwiązuje albo jaką daje wartość i dlaczego teraz.

   ### Kryteria akceptacji
   - [ ] Given ..., When ..., Then ... (każdy punkt da się sprawdzić)

   ### Definition of Done
   - [ ] CI zielone (lint, typy, testy, testy architektury)
   - [ ] Testy dla nowej logiki
   - [ ] PR po review (albo self-review) i zmergowany do `develop`
   - [ ] Działa na wdrożeniu develop
   - [ ] Zaktualizowane docs / AGENTS.md, jeśli trzeba
   - [ ] UI: audyt impeccable i sprawdzenie na telefonie

   ### Poza zakresem
   Opcjonalnie: czego to zadanie nie obejmuje.

   ### Obszar
   Frontend
   ```

   Dla `bug` sekcje idą w tej kolejności: Opis, Kroki do odtworzenia,
   Oczekiwane zachowanie, Faktyczne zachowanie, Środowisko (gałąź albo URL,
   urządzenie, przeglądarka), Dlaczego (wpływ i kogo blokuje), Kryteria
   akceptacji, Definition of Done (z punktem "Test regresyjny, który bez
   poprawki nie przechodzi"), Obszar.

   Obszar to jedno lub kilka z: Frontend, Backend, Worker, Infra, Design, Pitch.
5. Pokaż użytkownikowi tytuł i treść. Po akceptacji załóż zgłoszenie
   (repozytorium wynika z bieżącego katalogu, inne podaj przez `--repo`):

   ```bash
   gh issue create --title "feat(frontend): Filtrowanie wyjazdów po dacie" \
     --body-file "$TMPFILE" --project "TuttiTrip"
   ```

   `--project` wymaga zakresu `project` w tokenie gh. Jeśli go brakuje,
   uruchom `gh auth refresh -s project` albo pomiń flagę i dodaj zgłoszenie
   do projektu ręcznie. Etykietę `type:*` i typ zgłoszenia ustawi workflow.
6. Po około 30 sekundach sprawdź wynik:
   `gh issue view <numer> --json state,labels`. Jeśli zgłoszenie jest
   zamknięte z etykietą `invalid-format`, przeczytaj komentarz bota
   (`gh issue view <numer> --comments`), popraw treść przez
   `gh issue edit <numer> --title ... --body-file ...`, a workflow otworzy je
   ponownie.

Bez dopisków o AI: żadnych stopek w rodzaju "Generated with" w treści
zgłoszenia.
