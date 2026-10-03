# PersonChip

Osoba w podróży: awatar z inicjałami w kolorze `member-N`, imię, wiek lub rola.

- Sześć kolorów osób leży poza kątami barw stanów (zieleń, pomarańcz, bursztyn, czerwień): niebieski, fiolet, morski, malinowy, oliwkowy, grafit. Kolor osoby wolno użyć tylko jako awatar, kropkę na osi albo kreskę 3px. Nigdy jako tło stanu.
- W ciemnym motywie kolory osób są jasne, a inicjały ciemne (`on-member`).
- Profil bez konta: podwójny pierścień `muted-foreground` wokół awatara i słowo „profil”.
- Stos awatarów zawsze z tekstem obok, który wymienia imiona.

**Z rejestru**

- Stos: `npx shadcn@latest add @shadcn-space/avatar-08`.
- Imiona po najechaniu (desktop): `npx shadcn@latest add @aceternity/animated-tooltip`. Na telefonie zastąp tekstem obok stosu.