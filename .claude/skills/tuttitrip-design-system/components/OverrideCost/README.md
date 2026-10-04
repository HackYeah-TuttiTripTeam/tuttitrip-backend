# OverrideCost

Koszt decyzji hosta (wymuś, zablokuj, „mimo to jedziemy”) w trzech liczbach: sprawiedliwość, budżet, czas.

- Tytuł jest pytaniem w `title-2`, pod nim jedno zdanie, kogo to dotyczy (imiona osób na nie).
- Trzy kafelki: etykieta + wartość w `display` 20px. Pogorszenie `decline-soft`, poprawa `want-soft`, neutralne `muted`.
- Przycisk potwierdzenia w wariancie `ink` z kosztem w etykiecie. Nigdy zielony.
- Każdy override trafia do logu. Gdy budżet wychodzi poza margines, zamiast tej karty pokaż `ApprovalCard`.

**Z rejestru**

- Wartości: `npx shadcn@latest add @shadcn-space/number-ticker-02` (number-flow, waluta).
- Kontener: shadcn `drawer` (vaul) na telefonie, `dialog` na desktopie.