# ScreenInterviewChat

Wywiad tekstem z komponentami generowanymi przez agenta (AG-UI): agent nie pisze pytań do przepisywania, tylko wstawia kafle, chipy i suwaki do kliknięcia.

- Odpowiedzi zamieniają się w zwięzłe wiersze „✓ 5 osób · 2 profile”: historia to zebrane fakty, nie dymki rozmowy.
- Bieżące pytanie: „Asystent pyta” + nagłówek + komponent w kartce `tt-agui`. Kafle wyboru z opisem (`tt-choice`), chipy zakresów, suwak, wybór dat i osób. Jedno kliknięcie odpowiada.
- Pole tekstowe na dole jest dodatkiem („Dopisz, jeśli chcesz…”) z mikrofonem. Pisanie nigdy nie jest wymagane.
- „Zbuduj plan” w pasku działa od pierwszej odpowiedzi.

**AG-UI: kontrakt komponentów**

- Każdy komponent to narzędzie frontendu zadeklarowane przez klienta AG-UI i wywołane przez agenta (`ToolCallStart` / `ToolCallArgs` / `ToolCallEnd`). Klik użytkownika wraca jako wynik narzędzia. Proponowane narzędzia: `ask_choice` (kafle, 2–4 opcje z opisem), `ask_range` (chipy zakresów), `ask_slider` (wartość z jednostką), `ask_people` (osoby i wiek), `ask_dates`, `show_plan_preview`.
- Panel „Co już wiem” i wiersze ✓ to stan współdzielony (`StateSnapshot` / `StateDelta`). W Pydantic AI `StateDeps` z modelem Pydantic.
- Działania na zewnątrz (kalendarz, wyszukiwanie noclegów) to narzędzia z `requires_approval=True`; w UI renderują się jako `ApprovalCard`.
- Backend: `AGUIAdapter.dispatch_request()` z Pydantic AI w FastAPI. Klient: `@ag-ui/client` albo CopilotKit; komponenty to te same `tt-*` z tego systemu.