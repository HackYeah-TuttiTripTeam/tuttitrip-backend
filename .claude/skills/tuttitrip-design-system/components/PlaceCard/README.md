# PlaceCard

Miejsce w propozycji: nazwa, werdykt, uzasadnienie, kto za i kto przeciw, koszt dla składu grupy ze źródłem.

- Kartka płaska (`card`, obrys `border`, `radius-lg`), bez cienia.
- Kolejność: nazwa (`title-3`), werdykt w prawym górnym rogu, uzasadnienie (`body`, 1–2 zdania od asystenta), głosy (stos awatarów + `want-ink` / `decline-ink` z powodem z listy), linia trasy, cena w `figure` ze znacznikiem źródła.
- Liczby (cena, dystans, godziny) pochodzą z danych planu, nigdy z tekstu uzasadnienia.
- Dotknięcie otwiera bottom sheet z `RatingControl` i akcjami hosta (`OverrideCost`).

**Z rejestru**

- Karta: `npx shadcn@latest add card` bez `shadow`, `rounded-[20px]`.
- Wybór dnia „mimo to” na desktopie: `npx shadcn@latest add @aceternity/expandable-card-demo-standard` (rozwinięcie szczegółów), przestylowany na kartkę.