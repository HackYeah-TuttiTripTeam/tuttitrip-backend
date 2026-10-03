# PlanTimeline

Plan dnia jako trasa ze znaku: przystanki to kropki, przejazdy to linia z kropek, cel dnia to pierścień.

- Nagłówek dnia zwykłym zdaniem w `title-2` („Sobota, dzień 2”). Godziny w lewej kolumnie w `figure`.
- Szyna: kropki `route` co 8px między przystankami; przystanek = kropka `foreground` 10px; przerwa = kropka `route`; cel dnia (ostatni lub najważniejszy punkt) = pierścień `primary`.
- Punkty planu bez kart: nazwa `title-3`, pod nią meta. Jedna kartka na cały dzień, nie karta w karcie.
- Przejazd: ikona trasy, środek transportu, czas, cena w `figure`. Przerwa (drzemka, obiad) w `foreground` z ikoną księżyca: plan pokazuje tempo najwolniejszej osoby.
- Po przeliczeniu zmienione wiersze podświetlają się `accent` na 600ms.

**Z rejestru**

- Własny komponent (nie `@aceternity/timeline`: jego belka przy przewijaniu pasuje do landingu, nie do planu).
- Zmiany po przeplanowaniu: `npx shadcn@latest add @react-bits/AnimatedContent-TS-TW` na podmienionych wierszach.