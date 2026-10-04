# Settlement

Wydatki i rozliczenie: lista wydatków, niepewny odczyt do potwierdzenia, lista przelewów jak w Tricount.

- Wydatki jako lista w jednej kartce (nie stos kart): ikona, opis, kto płacił i kogo wyłączono, czas; kwota w `figure` do prawej.
- Niepewny odczyt: koło z przerywanym obrysem i przycisk z odczytaną kwotą („Potwierdź 38,97 zł”). Dotknięcie potwierdza, przytrzymanie otwiera edycję.
- Obca waluta: kwota oryginalna, pod nią przeliczenie w `caption` („≈ 186,20 zł po kursie z 3 paź”).
- Rozliczenie: „Do oddania”, dłużnik → wierzyciel z awatarami i kwotą. TuttiTrip nie robi przelewów: akcja to „Kopiuj listę”.

**Z rejestru**

- Zdjęcie paragonu: `npx shadcn@latest add @aceternity/file-upload`.
- Układ podsumowania do eksportu: `npx shadcn@latest add @shadcn-space/receipt-03` jako punkt wyjścia.