# BudgetBar

Budżet jako zakres od–do: pasmo komfortu, pierścień limitu (cel ze znaku) i zakreskowany margines.

- Tor `muted`, pasmo komfortu `want-soft`, wypełnienie wydane/zaplanowane, pierścień `primary` na limicie, margines (+10%) w ukośnym kreskowaniu `warning-soft`.
- Wypełnienie: poniżej „od” `foreground` (+ propozycje ulepszeń), w zakresie `primary`, w marginesie `warning` (wymaga `ApprovalCard`), ponad margines `destructive`.
- Liczba nad paskiem w `figure` („620 zł z 500–700”), pod spodem jedno zdanie („Zostało 80 zł do limitu.”).
- Kwoty po polsku: spacja tysięcy, przecinek dziesiętny, „zł” po liczbie.

**Z rejestru**

- Kwota: `npx shadcn@latest add @shadcn-space/number-ticker-02`.