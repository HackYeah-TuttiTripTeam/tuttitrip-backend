# BottomNav

Dolna nawigacja jako pływający dok: pigułka oderwana od krawędzi, aktywna pozycja wypełniona atramentem.

- Pięć pozycji: Plan, Miejsca, Asystent, Wydatki, Grupa. Ikona 20px + etykieta 12px; aktywna: pigułka `foreground` z tekstem `background`.
- Dok: `popover`, obrys `border`, `shadow-float` (jeden z niewielu cieni), margines `space-3` od krawędzi ekranu i nad `safe-area-inset-bottom`.
- Od 768px boczny pasek shadcn `Sidebar` z tymi samymi pozycjami.
- Lista podróży (`/trips`) jest ekranem startowym PWA i nie ma doku.

**Z rejestru**

- Wzór: `npx shadcn@latest add @aceternity/floating-dock` (wariant mobilny), z etykietami zawsze widocznymi i bez powiększania. `@react-bits/Dock-TS-TW` tylko na desktopie, jeśli w ogóle.