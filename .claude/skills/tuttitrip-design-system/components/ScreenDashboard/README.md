# ScreenDashboard

Ekran startowy po zalogowaniu (`/trips`, start PWA): co czeka na decyzję, nowa podróż jednym zdaniem, lista podróży.

- Kolejność to pilność. Najpierw „Czeka na Ciebie” (bilety decyzji i prośby o ocenę), potem pole nowej podróży z mikrofonem, na końcu lista podróży w jednej kartce.
- Decyzja w formie biletu (`ApprovalCard` w wersji skróconej) z przyciskiem `ink`.
- Wiersz podróży: nazwa (`title-3`), status (`Badge`), daty, stos awatarów. Profile osób bez konta mają pierścień.
- Bez doku: to ekran startowy. Dok pojawia się wewnątrz podróży.
- Pusty stan (pierwsze logowanie): samo pole „Dokąd, na ile, kto jedzie?” i przykład z Gdańskiem; z rejestru `@shadcn-space/empty-state-01` przestylowany na tokeny.