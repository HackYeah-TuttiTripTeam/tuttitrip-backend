# VerdictBadge

Werdykt algorytmu dla miejsca: obowiązkowo, pasuje, kultowe ale nie Twoje, pomiń. Pigułka 32px w kroju `display`.

- `must` → etykieta „Obowiązkowo” (po polsku; w kodzie zostaje `must`): pełne `primary`.
- `fits` „Pasuje”: `want-soft`.
- `iconic` „Kultowe, ale nie Twoje”: obrys przerywany w `foreground` i dwutonowa gwiazda. Przerywana linia = „nie Twoje”. Raz dziennie.
- `skip` „Pomiń”: tylko obrys `border` i tekst `muted-foreground`, bez przekreślenia.
- Nigdy bez uzasadnienia obok (`PlaceCard`). Decyzja hosta: dodatkowy `Badge solid` „Decyzja hosta”.