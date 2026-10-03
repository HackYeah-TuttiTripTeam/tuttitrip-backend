---
name: tuttitrip-design-system
description: Design system TuttiTrip (tokeny, kroje, ikony Keyline, komponenty tt-*, ekrany, zasady UX i copy po polsku). Używaj przy każdej pracy nad UI frontendu TuttiTrip — nowe ekrany, komponenty shadcn/ui, kolory, typografia, teksty interfejsu, dodawanie komponentów z rejestrów @react-bits, @aceternity, @shadcn-space.
---

# TuttiTrip design system

Zanim napiszesz lub zmienisz UI:

1. Przeczytaj `README.md` (zasady, ton, kolor, typografia, motyw trasy, ruch, rejestry, lista zakazanych efektów).
2. Dla komponentu przeczytaj `components/<Nazwa>/README.md` i obejrzyj `components/<Nazwa>/preview.html` (otwiera się w przeglądarce; style z `theme.css` i `components/bundle.css`).
3. Dla całego ekranu wzoruj się na `components/Screen*/`.
4. Kolory, odstępy, promienie i kroje bierz wyłącznie z `theme.css` / `tokens.json` (nazwy jak w shadcn: `bg-background`, `text-muted-foreground`, `bg-want-soft`, `font-heading`…). Nie wpisuj surowych wartości hex/oklch.
5. Ikony: `@keyline-icons/react` (nie lucide). Komponenty z rejestrów przestylowuj na tokeny i podmieniaj importy ikon.
6. Teksty UI po polsku według słownika z README (np. „Sprawdzenie planu”, nie „Linter”).

Pliki:
- `theme.css` — zmienne CSS (jasny / `.dark`), `@font-face`, mapowanie `@theme inline` dla Tailwind v4.
- `tokens.json` — te same tokeny jako dane, z opisem użycia każdego.
- `fonts/` — Funnel Display, Atkinson Hyperlegible Next i Mono (woff2, OFL).
- `components/bundle.css`, `components/index.d.ts` — odwzorowanie komponentów i kontrakt propsów.
- `assets/Logos`, `assets/Icons` — znak i wybrane ikony Keyline.
