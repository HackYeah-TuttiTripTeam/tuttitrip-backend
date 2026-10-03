# ScreenPlanBuilder

Ekran tworzenia planu (host): plan dnia, który można przesuwać, i stała informacja, ile jest wart dla każdego.

- Pod paskiem: osoby (stos awatarów), daty, numer wersji planu.
- Pasek trzech liczb: sprawiedliwość, najmniej zadowolona osoba, budżet dnia. Dotknięcie otwiera `FairnessMeter` / `FairnessLedger` / `BudgetBar` w bottom sheecie.
- Dni jako przełącznik segmentowy, plan dnia jako `PlanTimeline` z werdyktami przy punktach. Dotknięcie punktu otwiera `PlaceCard` z `OverrideCost`.
- Stały dół: „Sprawdź” (`LinterReport`) i „Wyślij propozycję” (primary). Po zmianie hosta liczby u góry przeliczają się (`@react-bits/CountUp`), a w czasie liczenia pokazuje się `PlanProgress`.