# Badge

Znacznik stanu: pigułka 28px z ikoną i słowem. Kolor nigdy nie występuje sam.

- Reguła linii: **pełne tło = fakt, linia przerywana = niepewne**. Niepotwierdzone wymaganie, niezweryfikowana cena, werdykt „Kultowe, ale nie Twoje” mają obrys przerywany i przezroczyste tło.
- Tony: `want` (spełnione, zatwierdzone), `danger` (niespełnione), `decline` (osoba na nie), `warning` (w marginesie budżetu), `neutral` (zweryfikowana, co-host), `solid` (host).
- Zweryfikowana: `neutral` + dwutonowa tarcza Keyline. Niezweryfikowana: `dashed` + link do oficjalnej strony obok.
- Tekst 1–2 słowa, `label` 13px.

**Z rejestru**

- `npx shadcn@latest add badge`, warianty `dashed`, `solid`, `want`, `decline`, `warning`, `danger` w `badgeVariants`, `rounded-full`.