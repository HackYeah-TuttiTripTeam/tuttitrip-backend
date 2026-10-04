# InterviewCard

Wywiad: jedno pytanie na ekran, zadane jako nagłówek, z kartą odpowiedzi pod spodem. Nie czat.

- Asystent nie ma dymków. Jego pytanie to nagłówek `title-2` w `display`, nad nim mały znacznik „Asystent pyta” (dwutonowe iskry Keyline, `primary`). Model opowiada, nie rozmawia.
- Pierwsze zdanie hosta zostaje na górze jako cytat w pigułce `secondary`: z niego powstaje cała podróż.
- Postęp: segmentowy pasek kresek (pełne `foreground`).
- Karta odpowiedzi: suwak z wartością w `figure` obok etykiety, chipy wyboru 44px. Pod spodem „Pomiń” (ghost) i „Dalej” (primary).
- „Co już wiem”: `muted` bez obrysu; na telefonie bottom sheet. „Budżet: nie ustalono” zamiast pustego pola. „Zbuduj plan teraz” działa od pierwszej odpowiedzi.

**Z rejestru**

- Postęp: `npx shadcn@latest add @shadcn-space/stepper-04` (segmentowa pigułka).
- Wejście pytania: `npx shadcn@latest add @react-bits/BlurText-TS-TW`, raz na pytanie, 400ms, wyłączone przy `prefers-reduced-motion`.
- Suwak z liczbą: `npx shadcn@latest add @shadcn-space/slider-04` (number-flow).
- Pole pierwszego zdania: `npx shadcn@latest add @aceternity/placeholders-and-vanish-input` z przykładami „Kraków, sobota z dziećmi…”.