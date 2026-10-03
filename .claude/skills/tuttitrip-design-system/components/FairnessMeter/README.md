# FairnessMeter

Sprawiedliwość jako wykres kropkowy: wiersz na osobę, kropka w kolorze osoby na trasie 0–100, zakreskowana strefa podłogi.

- Liczba miary w `metric` (Funnel Display 40px, cyfry tabelaryczne) i jeden znacznik stanu.
- Wiersze posortowane od najbardziej do najmniej zadowolonej osoby. Tor to kropki `route`, strefa 0–podłoga w ukośnym kreskowaniu `danger-soft` z krawędzią `destructive`, osoba to kropka 16px `member-N` z obwódką `card`. Wartość w `display` po prawej.
- Pod wykresem jedno zdanie z kodu: „Najmniej zadowolony: Kuba, 58. Rozrzut 23 punkty.” To jest „policzone, nie zgadnięte” pokazane formą.
- Osoba w strefie podłogi: znacznik `warning` „Kuba poniżej podłogi”.
- Szczegóły po dziedzinach: `FairnessLedger`.

**Z rejestru**

- Liczba: `npx shadcn@latest add @react-bits/CountUp-TS-TW` (od poprzedniej wartości po przeliczeniu, 600ms).
- Przesunięcie kropek po przeliczeniu: `motion` (`animate={{ left }}`), bez innych efektów.