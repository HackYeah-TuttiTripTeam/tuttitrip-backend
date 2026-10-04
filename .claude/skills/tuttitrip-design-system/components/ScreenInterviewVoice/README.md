# ScreenInterviewVoice

Wywiad głosem: jedno pytanie na ekranie, transkrypcja na żywo i od razu rozpoznane fakty do poprawienia dotknięciem.

- Pytanie asystenta jako nagłówek (26/32 display), nad nim „Asystent pyta” i postęp segmentowy.
- Transkrypcja w kartce `muted`, tekst `body` 18px; część jeszcze nie zrozumiana w `muted-foreground`.
- „Zrozumiałem”: znaczniki faktów od razu po rozpoznaniu. Pewne pełne (`want`), niepewne przerywane z pytaniem („Babcia Halina, wiek?”). Dotknięcie otwiera poprawkę.
- Poziom głosu to rząd kropek-kresek w `foreground` (motyw trasy), bez fal i poświat. Ostatnie słupki gasną.
- Duży mikrofon 88px `primary` z pierścieniem `want-soft`; po bokach „Pisz” (przejście do `ScreenInterviewChat` z tym samym stanem) i „Wstrzymaj”.
- Głos jest wejściem, nie rozmową: asystent nie czyta odpowiedzi na głos, chyba że użytkownik włączy to w ustawieniach.