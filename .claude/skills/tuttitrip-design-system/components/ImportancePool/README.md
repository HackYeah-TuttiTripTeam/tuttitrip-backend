# ImportancePool

Rozdzielanie stałej puli 10 punktów między nocleg, jedzenie, atrakcje, tempo i koszt.

- Punkty to kropki trasy: pełne `foreground`, puste z obrysem `route`. Liczba obok w `figure` (Funnel Display, cyfry tabelaryczne) jest źródłem prawdy.
- Przyciski +/− to koła 44px. „+” wyłączony, gdy pula się skończyła.
- 0 punktów to stan normalny, bez czerwieni. 8+ punktów: `Badge want` „Minimum gwarantowane”.

**Z rejestru**

- Zmiana liczby: `npx shadcn@latest add @react-bits/Counter-TS-TW` (przewijane cyfry), czcionka `display`.