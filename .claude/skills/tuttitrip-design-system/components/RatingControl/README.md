# RatingControl

Ocena miejsca: trzy duże kafle (chcę, obojętnie, nie chcę), a przy „nie chcę” powód jednym dotknięciem.

- Kafle 64px, obrys `input`, w spoczynku równe. Wybrany kafel wypełnia się pełnym kolorem: `primary`, `foreground` (obojętnie) albo `decline`. Pozostałe zostają obrysami.
- Powody pojawiają się dopiero po „nie chcę”, chipy 44px: za drogo, za daleko, nie mój klimat, za duży tłum, za trudne dla dziecka, inne. Wybrany: `decline-soft` z checkiem.
- Etykieta grupy zwykłą wielkością liter w `label` („Dlaczego nie?”), bez wersalików.
- Ten sam komponent w linku głosowym (osoba bez konta) i u hosta oceniającego za kogoś; wtedy nad nim `PersonChip`.

**Z rejestru**

- Baza: `npx shadcn@latest add toggle-group`, przestylowana na kafle.
- Wiele miejsc pod rząd jako talia do przesuwania (prawo = chcę, lewo = nie chcę): `npx shadcn@latest add @react-bits/Stack-TS-TW`, z kaflami jako dostępną alternatywą.