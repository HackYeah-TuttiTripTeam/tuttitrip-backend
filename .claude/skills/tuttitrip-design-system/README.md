TuttiTrip to planer wyjazdów rodzinnych i grupowych: „plan, po którym nikt nie czuje, że przegrał”. Interfejs ma pokazywać, że plan jest **policzony, a nie zgadnięty**: liczby, godziny, ceny i werdykty pochodzą z solvera i sprawdzenia planu, a model językowy pisze tylko pytania i uzasadnienia.

System stoi na shadcn/ui (Tailwind v4), ale celowo nie wygląda jak domyślny shadcn. Ma papierowe tło z zieloną nutą zamiast zinc, kroje Funnel Display i Atkinson Hyperlegible, przyciski-pigułki 44px, płaskie kartki bez cieni i motyw trasy ze znaku: kropki, przystanki i pierścień celu. `primary` i `destructive` mają wartości z produkcji.

## Zasady

1. **Telefon najpierw.** PWA w pionie, 360–430px, margines `space-4`, każdy cel dotyku ≥ `tap-min` (44px), nawigacja w pływającym doku.
2. **Każda osoba jest widoczna.** Awatar w kolorze `member-N`, imię, a przy decyzjach kto na tak i kto na nie.
3. **Liczba, potem zdanie.** Wynik w `metric`, obok jedno zdanie z kodu („Najmniej zadowolony: Kuba, 58.”).
4. **Pełne = fakt, przerywane = niepewne.** Linia przerywana oznacza wszystko, czego nie wiemy na pewno lub co „nie jest Twoje”: niepotwierdzone wymaganie, niezweryfikowaną cenę, werdykt „Kultowe, ale nie Twoje”, niepewny odczyt paragonu.
5. **Decyzja ma cenę.** Akcje, które kosztują innych, mają przycisk `ink` z kosztem w etykiecie i przechodzą przez `ApprovalCard`. Zieleń znaczy tylko „chcę” i bezpieczny krok dalej.

## Treść i ton

- Po polsku, na „Ty”, krótko, jak dobrze zorganizowany członek rodziny. „Babcia odpoczywa po obiedzie”, nie „Zaplanowano przerwę regeneracyjną”.
- Nagłówki to zwykłe zdania w kroju display, bez wersalikowych nadtytułów. Etykiety grup zwykłą wielkością liter w `label` („Dlaczego nie?”, „Co już wiem”).
- Przyciski to czasowniki: „Zbuduj plan teraz”, „Wymuś mimo to”, „Przenieś na wtorek”. Nie „OK”.
- Słownik UI: podróż, wyjście, host, co-host, członek, profil, werdykt, weto, decyzja hosta (override), sprawdzenie planu i problemy (linter i naruszenia w kodzie), przeplanowanie, rozliczenie. Werdykty: „Obowiązkowo”, „Pasuje”, „Kultowe, ale nie Twoje”, „Pomiń”. Głosy: „Chcę”, „Obojętnie”, „Nie chcę”. Powody: „Za drogo”, „Za daleko”, „Nie mój klimat”, „Za duży tłum”, „Za trudne dla dziecka”, „Inne”.
- Nie pokazuj nazw komponentów ani kodów reguł. Zamiast „Karta zatwierdzenia” pisz „Czeka na Twoją decyzję”; zamiast `closed_day` pisz „godziny otwarcia”.
- Liczby: „0,87”, „1 240 zł”, „09:30”, „sob 4 paź”. Bez emoji. Brak danych nazwany wprost: „Budżet: nie ustalono”.

## Kolor

- Neutralne to papier i atrament z zieloną nutą (hue 165): `background` papier, `card` biała kartka, `foreground` ciemnozielony atrament, `muted` / `muted-foreground` pomocnicze, `border` włoskowe linie, `input` obrys kontrolek ≥3:1.
- Cztery barwy stanów, każda z jednym znaczeniem: `primary`/`want` (zieleń: chcę, spełnione, bezpieczny krok), `decline` (pomarańcz: nie chcę, pogorszenie), `warning` (bursztyn: margines budżetu, decyzja czeka), `destructive` (czerwień: niespełnione, błąd, usuwanie). Każda ma `*-soft` (tło) i `*-ink` (tekst na tle) ≥4,5:1 w obu motywach.
- `info` i fiolet zniknęły: „zweryfikowana” to `neutral` + tarcza, „kultowe” to obrys przerywany + gwiazda.
- Osoby: `member-1…6` (niebieski, fiolet, morski, malinowy, oliwkowy, grafit) leżą poza kątami barw stanów. Tylko awatar, kropka na osi albo kreska 3px. Nigdy jako tło stanu. W ciemnym motywie jasne z ciemnymi inicjałami.
- `ink` (atrament jako tło przycisku i aktywnej pozycji doku) to drugi akcent obok zieleni.
- `route`: kropki trasy, czysto dekoracyjne.
- Bez gradientów, poświat i szkła.

## Typografia

Trzy kroje, wszystkie na licencji OFL, hostowane lokalnie jako zmienne woff2 z polskimi znakami (`fonts/`, podzbiór latin + latin-ext). Działają offline w PWA, bez Google Fonts:

- **Funnel Display** (`display`, 300–800): nagłówki i wszystkie liczby (`metric`, `figure`, cyfry tabelaryczne `tnum`). Ma charakterystyczne, „lejkowate” zakończenia i rzadko pojawia się w aplikacjach na shadcn.
- **Atkinson Hyperlegible Next** (`sans`, 200–800): cała treść. Krój Braille Institute dla osób słabiej widzących, z wyraźnie różnymi I/l/1 i O/0. To decyzja produktowa: plan czyta babcia i dziecko. Cyfry w treści mają przekreślone zero.
- **Atkinson Hyperlegible Mono** (`mono`): tylko kody zaproszeń.

Skala:

- `display` 44/44 800: tytuł podróży.
- `title-1` 32/36: ekran.
- `title-2` 22/28: sekcja, dzień, karta, sheet.
- `title-3` 18/24: miejsce, wiersz.
- `metric` 40/40 800 i `figure` 16/24 650: liczby.
- `body` 17/26: treść.
- `body-sm` 15/22: metadane.
- `label` 15/20 600: przyciski, chipy, etykiety.
- `caption` 13/18: źródła, osie. To minimum.

Zainstaluj kroje przez `@font-face` z plików `fonts/*.woff2` (`font-display: swap`) i w `@theme`: `--font-sans`, `--font-heading` (display), `--font-mono`. Alternatywnie `@fontsource-variable/funnel-display`, `@fontsource-variable/atkinson-hyperlegible-next`, `@fontsource-variable/atkinson-hyperlegible-mono`.

## Kształt, przestrzeń, głębia

- Siatka 4px. Margines ekranu `space-4`, padding kartki `space-4` (desktop `space-5`), odstęp sekcji `space-6`, dni `space-8`.
- Promienie większe niż w shadcn: kartki `radius-lg` 20px, bottom sheet i karta zatwierdzenia `radius-xl` 28px, pola `radius-md` 12px, przyciski, chipy i awatary `radius-full`.
- **Kartki leżą płasko**: `card` + obrys `border`, bez cienia. Jeden poziom kartek na ekran; wiersze w środku to listy z włoskowymi liniami (plan dnia, wydatki, wymagania, problemy). `shadow-float` tylko dla elementów pływających: dok, bottom sheet, tooltip.
- Focus: obrys 2px `ring` z odstępem 2px.

## Motyw trasy

Znak TuttiTrip (duża kropka, kropki trasy, pierścień celu) jest językiem wykresów i list:

- **Kropki `route`** (2px co 8px) łączą przystanki: oś dnia, separator ceny w karcie miejsca, tory wykresu sprawiedliwości.
- **Przystanek** = kropka 10px `foreground`; zrobione = `primary`; czekające = `route`.
- **Cel** = pierścień 18px w `primary`: cel dnia, limit budżetu, bieżący krok liczenia planu.
- **Osoba na osi** = kropka 16px w `member-N` (`FairnessMeter`).
- **Bilet**: perforacja z wycięciami oddziela decyzję od przycisków (`ApprovalCard`).

## Ruch

- Animujemy tylko to, co policzył kod: przeliczenie liczb (CountUp, number-flow), przesunięcie osób na osi, pojawienie się problemów, kroki liczenia planu. 150–250ms, `cubic-bezier(.4,0,.2,1)`; liczby do 600ms.
- `prefers-reduced-motion`: bez przejść, wartości od razu.
- **Zakazane** (wygląd szablonu AI): tła i efekty bez danych, czyli Aurora, Background Beams, Sparkles, Meteors, Spotlight, Lamp, Vortex, Glowing Effect, Shine/Moving Border, gradientowy tekst, karty 3D i tilt, Hero Parallax. Również z rejestrów poniżej.

## Rejestry shadcn

Poza `shadcn` korzystamy z czterech rejestrów z oficjalnego indeksu. Działają bez konfiguracji: `npx shadcn@latest add @namespace/nazwa`. Każdy komponent z rejestru przed użyciem przepinamy na tokeny tego systemu: bez ich kolorów, gradientów i cieni, ikony na Keyline, kroje na nasze.

| Rejestr | Do czego | Elementy |
| --- | --- | --- |
| `@react-bits` (reactbits.dev, wariant `-TS-TW`) | ruch liczb i list | `CountUp`, `Counter`, `AnimatedList`, `AnimatedContent`, `BlurText`, `Stack` (talia ocen) |
| `@aceternity` (ui.aceternity.com) | stany przycisków i kroków | `stateful-button`, `multi-step-loader`, `animated-tooltip`, `floating-dock`, `placeholders-and-vanish-input`, `file-upload`, `expandable-card-demo-standard` |
| `@shadcn-space` (shadcnspace.com) | gotowe bloki aplikacji | `stepper-04`, `slider-04`, `number-ticker-02`, `avatar-08`, `receipt-03`, `empty-state-01…06`, `login-05`, `drawer-01` |
| `@keyline` / `@keyline-icons/react` (keylineicons.com) | ikony | stroke, rounded; two-tone dla gwiazdy, tarczy i iskier asystenta |

Rejestry zależą od `lucide-react`. Po dodaniu podmień importy na `@keyline-icons/react`; nazwy w większości się pokrywają (`ThumbsUp`, `CloudRain`, `MapPin`, `Check`, `X`).

## Ikony

Keyline Icons (MIT): siatka 24×24, linia 2px, zaokrąglone rogi. 20px w kontrolkach, 16px w znacznikach i wierszach. Kolor zawsze z `currentColor`. Wybrany zestaw z przypisaniami: `assets/Icons`. Ikona nigdy nie stoi sama w miejscu stanu; przyciski ikonowe mają `aria-label`.

## Logo

Znak: `assets/Logos/tuttitrip-mark.svg` (z `favicon.svg` produkcji) w `brand` / `brand-ink`. Wordmark nie istnieje: „TuttiTrip” składaj w Funnel Display 800 z rozstrzałem −0,035em obok znaku.

## Komponenty

Statyczne odwzorowania w HTML i `components/bundle.css` (klasy `tt-*`); propsy w `components/index.d.ts`. Każdy README mówi, z jakiego elementu shadcn albo rejestru zacząć.

- Akcje i stan: `Button`, `Badge`, `VerdictBadge`.
- Ludzie: `PersonChip`.
- Preferencje i wywiad: `RatingControl`, `ImportancePool`, `InterviewCard`.
- Planowanie: `PlaceCard`, `PlanTimeline`, `PlanProgress`, `FairnessMeter`, `FairnessLedger`, `LinterReport`, `OverrideCost`, `ApprovalCard`, `BudgetBar`.
- Miejsca: `RequirementCheck`.
- W podróży: `ReplanBar`.
- Wydatki: `Settlement`.
- Nawigacja: `BottomNav`.

## Ekrany

Przykładowe ekrany w grupie „Ekrany” składają komponenty w całość i są wzorcem układu. Telefon to 390×844, a strony publiczne to desktop 1200px.

- `ScreenLanding`: strona główna dla niezalogowanych.
- `ScreenAbout`: o projekcie.
- `ScreenDashboard`: start po zalogowaniu.
- `ScreenSettings`: ustawienia.
- `ScreenPlanBuilder`: tworzenie planu.
- `ScreenInterviewVoice`: wywiad głosem.
- `ScreenInterviewChat`: wywiad tekstem z komponentami AG-UI.
- `ScreenDecisions`: decyzja o propozycji.

Każdy ekran ma README z regułami układu.
