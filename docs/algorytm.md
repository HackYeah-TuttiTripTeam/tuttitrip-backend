> Kanoniczna specyfikacja algorytmu dla `src/tuttitrip/planning/**/logic`. Zastępuje załącznik `algorytm-rekomendacji.md` z issue #27.
> Poniżej treść dokumentu `algorytm-final.md` (v1.0) bez zmian.

# TuttiTrip — finalny algorytm sprawiedliwości (v1.0)

Jeden algorytm dla każdego `n ≥ 1`. Grupa i pojedynczy użytkownik nie mają osobnych ścieżek kodu:
plan jednoosobowy to szczególny przypadek tego samego równania, a zarazem **punkt odniesienia** dla każdego członka grupy.

Implementacja referencyjna (Python 3.9, sama biblioteka standardowa) i testy: folder `algorytm\`.
Zastępuje propozycje z `algorytm-rekomendacji.md` (tam zostają odrzucone warianty i pomysły rozszerzeń).

---

## 1. Co znaczy „sprawiedliwie" — cztery poziomy

| Poziom | Pytanie | Mechanizm | Dla jednej osoby (n=1) |
|---|---|---|---|
| Między osobami | Czy ktoś „przegrał"? | Dobrobyt Nasha `Σ wᵢ·ln(1+uᵢ)` + podłogi `fᵢ` | Znika (jedna składowa, rosnąca) |
| Wewnątrz osoby | Czy ważna dla niej sprawa nie została poświęcona? | Średnia **geometryczna** domen z puli ważności | **Działa w pełni** |
| Między dniami | Czy dzień 3 nie jest pusty? | Nasycenie liczone **dziennie**, potem uśrednione | **Działa w pełni** |
| Wobec pieniędzy i danych | Czy plan nie udaje, że jest tańszy lub pewniejszy? | Zawyżenie niezweryfikowanych cen, jawna zgoda na przekroczenie | **Działa w pełni** |

Wniosek: użytkownik solo nie jest „zdegenerowaną grupą". Dostaje te same trzy zabezpieczenia, które nie wymagają drugiej osoby.

---

## 2. Dane wejściowe

Osoba `i`: waga `wᵢ` (dziecko 2, dorosły 1; `max/min ≤ 3`), profil zainteresowań `Iᵢ`, pula ważności `aᵢⱼ` (10 punktów na domeny: nocleg, jedzenie, atrakcje, tempo, koszt), komfort (odcinek `sᵢ`, dystans dzienny `Dᵢ`, czas aktywny `Aᵢ`, schody, kolejki), głosy `vᵢₚ ∈ {−1,0,+1}`, podłoga `fᵢ`, minima tagów (np. „indyjska").
Wyjazd: dni, budżet `B_od ≤ B_do`, margines `flex` (`B_max = B_do·(1+flex)`), noclegi z kontraktem 3-stanowym, weta, „must".

Domeny **aktywne**: `nocleg` tylko gdy są noclegi. Pula jest renormalizowana do aktywnych domen: `aᵢⱼ ← aᵢⱼ / Σₖ aᵢₖ`. Wyjście jednodniowe działa bez żadnej zmiany kodu.

---

## 3. Równania

### E0. Ograniczenia twarde (nigdy nie łagodzone)
weto · „must" · godziny otwarcia i okno dnia · budżet `c(P) ≤ B_max` · dzienny dystans `≤ 1,5·Dᵢ` dla każdego · miejsce odrzucone, gdy `schodyₚ·wrażliwośćᵢ ≥ 0,9` albo odcinek `> 1,5·sᵢ`.
Ograniczenia „schody” i „wózek” z preferencji osoby dają `wrażliwośćᵢ = 1` (liczone przy odczycie, `effective_stairs_sensitivity`), a „wózek” solver czyta wprost jako twarde wykluczenie miejsc ze `schodyₚ > 0` (decyzja dla #45).

### E1. Użyteczność miejsca (bez kosztu)
```
mᵢₚ = (1−ρ)·cos(Iᵢ, Tₚ) + ρ·(vᵢₚ+1)/2      ρ = 0,7 gdy jest głos, inaczej mᵢₚ = cos;  brak danych → 0,5
eᵢₚ = 0,6·min(1, dₚ/sᵢ) + 0,2·schodyₚ·wrażliwośćᵢ + 0,2·min(1, kolejkaₚ/cierpliwośćᵢ)
λₘ = (aᵢ,dom(p) + 0,1)/Z     λₑ = (aᵢ,tempo + 0,1)/Z     (Z normalizuje do 1)
uᵢₚ = min( 100, 100·(mᵢₚ+ε)^λₘ · (1−eᵢₚ+ε)^λₑ )          ε = 0,01
```
Koszt **celowo nie występuje** na poziomie miejsca. W propozycji był tam i na poziomie planu, co liczyło go podwójnie.
Teraz cenę widzi wyłącznie E2, a cena jednego biletu wpływa na wybór miejsca tylko przez budżet.

### E2. Zadowolenie z domeny `qᵢⱼ(P)` w skali 0–100
```
atrakcje :  (1/D) Σ_d 100·(1 − exp(−0,6 · Σ_{p∈A_d} (τₚ/90)·uᵢₚ/100))
jedzenie :  (1/D) Σ_d 100·(1 − exp(−1,2 · Σ_{p∈F_d} uᵢₚ/100))
tempo    :  100·(1 − min(1, (1/D) Σ_d [ ½·(L_d−Dᵢ)⁺/Dᵢ + ½·(A_d−Aᵢ)⁺/Aᵢ ]))
koszt    :  100 dla c ≤ B_od;  100→60 liniowo do B_do;  60→0 liniowo do B_max
nocleg   :  100·S_h      S_h = Π(wymogi twarde) · średnia(wymogi miękkie);  spełnione 1 · niepotwierdzone 0,4 · niespełnione 0
```
Nasycenie jest wklęsłe **w każdym dniu osobno**, więc 5. atrakcja jednego dnia daje mniej niż 1. atrakcja dnia pustego. To jest sprawiedliwość między dniami, także dla jednej osoby.

### E3. Dobrobyt osoby
```
uᵢ(P) = Π_j (1 + qᵢⱼ)^aᵢⱼ − 1         (ważona średnia geometryczna; 0 ≤ uᵢ ≤ 100)
```
Przykład: `a = (½, ½)`. Plan `(q₁,q₂) = (100, 0)` daje `uᵢ ≈ 9`, a `(50, 50)` daje `uᵢ = 50`. Średnia arytmetyczna dałaby w obu przypadkach 50.
Domena z `aᵢⱼ = 0` nie wpływa na wynik.

### E4. Punkt odniesienia — „co dostałbym sam"
```
u*ᵢ = max_P uᵢ(P)   (ten sam solver, n = 1, udział w budżecie 1/N)
rᵢ  = min(1, (uᵢ + 10) / (u*ᵢ + 10))        ← pokazywane w UI jako „x% Twojego maksimum"
fᵢ^eff = min(fᵢ, 0,6·u*ᵢ)                   ← podłoga nie może żądać więcej, niż osoba mogłaby mieć sama
```
Dla `n = 1`: `rᵢ ≡ 1`, `fᵢ^eff = 0`.

### E5. Cel grupy
```
W(P) = Σᵢ wᵢ · φ_α(uᵢ),     φ₁(u) = ln(1+u),    φ_α(u) = (1+u)^(1−α)/(1−α)   (α ≠ 1)
V(P) = Σᵢ [ (fᵢ^eff − uᵢ)⁺ / fᵢ^eff   +   ½·(brakujące dni bez „własnego" miejsca)   +   Σ_tag (k − mam)⁺ / k ]
J(P) = W(P) − 1000·V(P)                       maksymalizuj J przy ograniczeniach E0
```
- `α = 1` (Nash, proporcjonalna sprawiedliwość) jest domyślne. Suwak 0 → 3 daje „więcej łącznej korzyści" ↔ „więcej równości".
- „Własne miejsce" w dniu: `mᵢₚ ≥ 0,6`.
- Minimum z puli: tag z `aᵢ,dom ≥ θ = 0,4` wymusza `k = 1 + ⌊(aᵢ,dom − θ)/(1−θ)·2⌋` miejsc, ograniczone dostępnością.
- Podłogi, własne miejsca i minima są **miękkie z karą 1000**. Gdy się nie da, plan i tak powstaje, a naruszenie jest raportowane (`floors_missed`, `violation`). Nie ma ślepego zaułka „brak planu".
- Remis: `(J, niższy koszt, leksykograficznie po id)`. Osoby są sortowane po id, więc wynik nie zależy od kolejności wejścia. Hash planu (SHA-256, 12 znaków) jest powtarzalny.

### E6. Koszt i zgoda na przekroczenie budżetu
```
c(P) = Σ_miejsca Σ_osoby cena · (1 + δ·[cena niezweryfikowana]) + noce·cena_noclegu       δ = 0,15
```
Wyliczamy plan `P_flex` (limit `B_max`). Jeśli `c ≤ B_do`, koniec. W przeciwnym razie liczymy też `P_strict` (limit `B_do`) i `P_tańszy` (limit `c − 5%·B_do`). Przekroczenie zatwierdzamy (`needs_approval = true`) tylko gdy zachodzą oba warunki:
1. ktoś z silną preferencją zyskuje `≥ 8 pkt` **albo** `min rᵢ` rośnie o `≥ 0,05`,
2. nie ma tańszej alternatywy: `W(P_flex) − W(P_tańszy) ≥ 0,03`.

Cena za punkt: `κ = (c_flex − c_strict) / max ΔUᵢ` w zł/pkt, pokazywana organizatorowi.

---

## 4. Przypadek jednej osoby (n = 1)

1. `W = w·ln(1+u)` jest rosnące w `u`, więc **argmax J = argmax u**. Waga `w` nie ma znaczenia (test). Algorytm po prostu maksymalizuje jej zbalansowaną użyteczność.
2. `u* = u`, więc `r = 1` i miernik sprawiedliwości (Jain) jest zawsze 1. W UI nie pokazujemy go jako „100% sprawiedliwości", bo to bezwartościowa liczba. Zamiast tego pokazujemy **wykres zadowolenia z domen `qⱼ`** („Twój plan: nocleg 85, jedzenie 72, atrakcje 78, tempo 90, koszt 60") oraz najsłabszą domenę.
3. Podłogi `fᵢ = 0` (nie ma przed kim chronić). Zostają: własne miejsce w każdym dniu, minima z puli, balans domen i dni.
4. Koszt, weta, „must", kontrakt noclegu i zawyżanie niezweryfikowanych cen działają tak samo.
5. **Spójność grupa ↔ solo** (test): grupa `n` identycznych klonów dostaje dokładnie ten sam plan (ten sam hash) co osoba sama, przy proporcjonalnym budżecie.

---

## 5. Dlaczego te wybory

| Decyzja | Uzasadnienie | Odrzucone |
|---|---|---|
| Nash `α=1` jako domyślne | Pareto-optymalne, skalowo niezmiennicze, intuicyjne („nikt nie dostaje 0"). Przy `s=1` dokładnie niezmiennicze na zmianę skali osoby, więc nie trzeba normalizować przed optymalizacją | Utylitaryzm (poświęca słabszego), leximin (zbyt sztywny, jedna skrajna osoba steruje całością) |
| `rᵢ` tylko do podłogi i wyświetlania | Dla `α=1` normalizacja nie zmienia wyboru planu, a odsłania „x% mojego maksimum" zrozumiałe dla człowieka | Kalai–Smorodinsky jako cel (wymaga pełnych solverów na każdy suwak) |
| Średnia geometryczna domen | Chroni jedną osobę przed planem „samo muzeum, zero jedzenia". Nie wymaga nikogo więcej | Średnia arytmetyczna z propozycji |
| Nasycenie dzienne | Jedno równanie daje balans dni i malejące korzyści | Osobna kara za nierówność dni |
| Podłoga ≤ 60% `u*` | Nikt nie wymaga więcej, niż mógłby dostać sam. To nie zależy od ręcznego strojenia | Podłoga stała |
| Kara zamiast niewykonalności | Zawsze zwracamy plan, a naruszenia są jawne | Twarde podłogi (`brak planu`) |
| Koszt tylko na poziomie planu | Brak podwójnego liczenia | Koszt w `uᵢₚ` |

---

## 6. Parametry domyślne

| Symbol | Wartość | Znaczenie |
|---|---|---|
| α | 1 | suwak sprawiedliwości (0 użyteczność, 1 Nash, 3 prawie egalitarnie) |
| κ_atr, κ_jedz | 0,6 · 1,2 | nasycenie dzienne |
| τ_ref | 90 min | referencyjny czas atrakcji |
| ε, λ_floor | 0,01 · 0,1 | stabilność średniej geometrycznej |
| ρ (głos) | 0,7 | waga jawnego głosu wobec profilu |
| δ | 0,15 | zawyżenie niezweryfikowanej ceny |
| ρ_unc | 0,4 | punkty za wymóg „niepotwierdzony" |
| θ | 0,4 | próg silnej preferencji |
| s (wygładzanie) | 10 | w `rᵢ` |
| podłoga | ≤ 0,6·u* | max względem własnego maksimum |
| kara naruszeń | 1000 | miękkość podłóg |
| koszt: komfort | 60 | `q_koszt` na `B_do` |
| dobry powód | 8 pkt · 0,05 · 0,03 | progi zgody na przekroczenie |

Wszystkie wartości są **niekalibrowane** — to rozsądne punkty startowe, do strojenia na danych użytkowników.

---

## 7. Wyniki uruchomienia (dane demonstracyjne)

Grupa: Ty, Kasia (6), Tomek (13), Babcia; 3 dni, budżet 1300–1700 zł, weto na restaurację morską. Hash planu `9defef8adc3f`, koszt 1475 zł, nocleg `apartament_basen` (jedyny spełniający twardy wymóg basenu).

| Osoba | u* (sama) | u (w grupie) | r | podłoga |
|---|---|---|---|---|
| Babcia | 64,6 | 63,6 | 99% | 30,0 |
| Kasia | 62,2 | 55,9 | 91% | 35,0 |
| Tomek | 43,8 | 40,8 | 94% | 26,3 |
| Ty | 60,3 | 51,4 | 87% | 30,0 |

Jain(r) = 0,998, `min r` = 0,87, brak naruszeń podłóg. Tomek ma pierwszeństwo restauracji indyjskiej (minimum z puli). Plan: dzień 1 muzeum + indyjska · dzień 2 Hevelianum + bar mleczny + kawiarnia w ogrodzie · dzień 3 park + planszówki + pizzeria.

Tryb solo (2 dni, 500–800 zł): każdy dostaje `r = 100%`, w każdym dniu jest jedzenie i atrakcja (test). Przykład, Ty: `u = 82,1`, zawyżona cena niezweryfikowanego Westerplatte widoczna (514 zł zamiast 500 zł).

Budżet 900–1100 zł dla tej samej grupy: plan kosztuje 1198 zł (+98 zł ponad `B_do`), `needs_approval = true`, `κ ≈ 18,7 zł/pkt`.

**Uczciwa uwaga:** w tym demo α prawie nie zmienia planu (Jain 0,998 dla α = 0, 1 i 3). Plan ogranicza tempo najwolniejszych osób, a nie konflikt interesów, bo w modelu wszyscy idą razem do każdego miejsca. Suwak ma realny wpływ dopiero, gdy miejsca się wykluczają (np. dwa równoległe wybory z różnym zyskiem dla różnych osób). Gwarantowane jest tylko to, że plan dla danego α maksymalizuje własny cel (test).

---

## 8. Testy (26, `py -3 -m unittest -v test_fairness`, ok. 6 s)

- **Redukcja n=1:** solver = brute force (w granicach 0,5%) · solo maksymalizuje własne `u` · `r = 1` · waga nic nie zmienia · klony = solo.
- **Grupa:** solver ≈ brute force · determinizm (także między procesami z różnym `PYTHONHASHSEED`) · niezależność od kolejności osób i miejsc · podłogi spełnione i `≤ 0,6·u*` · `uᵢ ≤ u*ᵢ`.
- **Twarde ograniczenia:** weto, „must", budżet `≤ B_max`, wyjście jednodniowe bez noclegu.
- **Sprawiedliwość:** większa waga osoby nie pogarsza jej `u` · plan dla każdego α maksymalizuje swój cel · zasada Pigou–Daltona dla `φ_α` · balans domen (średnia geometryczna) · minimum z puli.
- **Dane:** zawyżenie niezweryfikowanej ceny · stany kontraktu noclegu · indeks Jaina.

Uruchomienie dema: `py -3 demo.py` w folderze `algorytm\` (zapisuje `wyniki_demo.json`).

---

## 9. Ograniczenia (wprost)

- Wszyscy uczestniczą we wszystkich miejscach. **Dzielenie grupy** (babcia odpoczywa, reszta idzie dalej) nie jest zaimplementowane i zmieniłoby model kosztów.
- Solver to deterministyczne wyszukiwanie lokalne (dodaj/usuń/zamień/przenieś/para), nie dowód optymalności. Na instancjach testowych zgadza się z brute force, na większych nie ma gwarancji. Produkcyjnie: CP-SAT z linearyzacją logarytmu stycznymi (patrz `algorytm-rekomendacji.md`).
  Limit pracy to liczba ocenionych planów, nie czas. Gdy limit kończy szukanie, wynik jest stanem lokalnym, nie optimum (`Telemetry.exhausted`); umieszczanie „must” limitu nie uwzględnia.
- Harmonogram dnia to heurystyka (kolejność wg terminu zamknięcia lub otwarcia) bez macierzy czasów dojazdu; stała przesiadka `transferₚ`.
- Nocleg: jedna baza na wszystkie noce.
- Parametry niekalibrowane. Dane w `demo_data.py` są wymyślone, to nie są dane o miejscach w Gdańsku.
- Poza zakresem tej wersji (pomysły z rekomendacji, które pozostają ważne): deszcz i stabilność planu, MMR / pokrycie w preselekcji, uczenie gustu z powodów odrzuceń, pamięć sprawiedliwości między dniami, mediana ocen bayesowska.

---

## 10. Mapowanie na interfejs

| Wynik algorytmu | Element UI |
|---|---|
| `rᵢ` per osoba, `min r` | Słupki w prawym panelu „Co już wiem", znacznik podłogi |
| `qᵢⱼ` | Wykres domen (u solo zamiast Jaina) |
| `floors_missed`, `violation`, `conflicts` | Ostrzeżenie w prawym panelu, zawsze z powodem |
| `needs_approval`, `κ`, `ponad_B_do` | Okno zgody organizatora z ceną za punkt (Confirm-Destroy: bez zamknięcia klikiem w tło) |
| `explain()` | Karta „dlaczego to miejsce" dla każdej osoby (dopasowanie, wysiłek, użyteczność) |
| `plan_hash` | Etykieta powtarzalności planu |
