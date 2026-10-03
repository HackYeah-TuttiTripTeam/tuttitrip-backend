# PlanProgress

Liczenie planu pokazane jako trasa kroków: co kod już policzył, co sprawdza teraz, co zostało dla modelu.

- Kroki to przystanki: zrobione = kropka `primary`, bieżący = pierścień, czekające = kropka `route` i tekst `muted-foreground`.
- Kroki z prawdziwymi liczbami („Zebrano 184 miejsca”), w kolejności S1 → S2: najpierw kod (miejsca, zadowolenie, sprawdzenie), na końcu „Piszę uzasadnienia”.
- Zastępuje spinner wszędzie, gdzie przeliczenie trwa dłużej niż 400ms.

**Z rejestru**

- Logika kroków: `npx shadcn@latest add @aceternity/multi-step-loader`, wygląd zastąpiony trasą (bez pełnoekranowego rozmycia).