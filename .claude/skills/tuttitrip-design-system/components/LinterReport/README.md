# LinterReport

Sprawdzenie planu: porównanie liczby problemów planu wklejonego z innego narzędzia z planem TuttiTrip i lista z akcją naprawy.

- W UI „Sprawdzenie planu” i „problemy”, nie „linter” i „naruszenia”.
- Nagłówek to dwie kolumny: „Plan wklejony z czatbota 3” i „Plan TuttiTrip 0” (`metric`; druga kolumna na `want-soft`). To liczba do pokazania jury.
- Wiersze bez kolorowych teł: ikona w kolorze stopnia (`destructive`, `warning`, `muted-foreground`), tytuł `label`, pod nim gdzie i kogo dotyczy oraz nazwa reguły zwykłym słowem („godziny otwarcia”). Gdzie się da, przycisk naprawy („Przenieś na wtorek”).
- Identyfikatory reguł (np. `closed_day`) zostają w danych i w panelu admina, nie w UI.

**Z rejestru**

- Pojawianie się problemów po wklejeniu: `npx shadcn@latest add @react-bits/AnimatedList-TS-TW`.
- Liczby: `@react-bits/CountUp-TS-TW`.