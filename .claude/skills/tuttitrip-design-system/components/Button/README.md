# Button

Pigułka 44px: jedyny kształt przycisku w aplikacji, w odróżnieniu od prostokątnego `Button` z shadcn.

- `primary` (zieleń): jedna bezpieczna akcja na ekran, „Zbuduj plan teraz”, „Wyślij propozycję”, „Dalej”.
- `ink` (atrament `foreground` na `background`): każda akcja, która coś kosztuje innych: „Wymuś mimo to”, „Zatwierdź przekroczenie”. Koszt w etykiecie małą cyfrą w `display`: „Wymuś mimo to −0,08”. Zieleń nigdy nie oznacza kosztownej decyzji.
- `outline` (obrys `input` 1,5px), `secondary`, `ghost` (anulowanie), `destructive` (usuwanie po dialogu).
- Zawsze ≥ `tap-min`; przycisk ikonowy jest kołem 44px z `aria-label`.
- Stan pracy: `aria-busy`, etykieta mówi, co się dzieje („Liczę plan…”).
- Etykieta: czasownik, wielka litera tylko na początku, bez kropki.

**Z rejestru**

- Baza: `npx shadcn@latest add button`, potem warianty `ink`, `rounded-full`, `h-11` w `buttonVariants`.
- „Zbuduj plan teraz” i „Zatwierdź” w karcie zatwierdzenia: `npx shadcn@latest add @aceternity/stateful-button` (ładowanie → sukces), przestylowany na tokeny.