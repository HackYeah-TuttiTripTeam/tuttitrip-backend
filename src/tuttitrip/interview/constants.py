"""Fixed values of the interview: question order, tool names, limits."""

from typing import Final

from tuttitrip.interview.schemas import CardKind, QuestionField

CARD_OF_FIELD: Final[dict[QuestionField, CardKind]] = {
    QuestionField.DESTINATION: CardKind.CHOICE,
    QuestionField.DATES: CardKind.CHOICE,
    QuestionField.PEOPLE: CardKind.FAMILY_BUILDER,
    QuestionField.BUDGET: CardKind.BUDGET_RANGE,
    QuestionField.PACE: CardKind.SLIDER,
    QuestionField.IMPORTANCE: CardKind.DOT_POOL,
    QuestionField.REQUIREMENTS: CardKind.REQUIREMENT_TOGGLES,
    QuestionField.INTERESTS: CardKind.SWIPE,
    QuestionField.DIET: CardKind.CHOICE,
}
"""The card each question is shown on."""

GROUP_FIELDS: Final = (
    QuestionField.DESTINATION,
    QuestionField.DATES,
    QuestionField.PEOPLE,
    QuestionField.BUDGET,
)
"""Questions about the whole trip, in order of plan impact. Asked until filled."""

PERSON_FIELDS: Final = (
    QuestionField.PACE,
    QuestionField.IMPORTANCE,
    QuestionField.REQUIREMENTS,
    QuestionField.INTERESTS,
    QuestionField.DIET,
)
"""Questions about people, in order of plan impact, each asked once per person."""

SLOWEST_ONLY: Final = frozenset({QuestionField.PACE, QuestionField.REQUIREMENTS})
"""Person questions that matter only for the slowest person (the group is as slow)."""

SHOW_CARD_TOOL: Final = "show_card"
"""Name of the tool that puts a card on the screen."""

DEFAULT_CURRENCY: Final = "PLN"
"""Currency of a budget the host gives without naming one."""

MAX_FREE_TEXT: Final = 300
"""Longest free text the classification tools accept."""

TOKENS_PER_PRICE_UNIT: Final = 1_000_000
"""Token prices are given per million tokens."""


MAX_TRIP_DAYS: Final = 60
"""Longest trip the assistant plans in one go."""

MAX_USER_TEXT: Final = 4000
"""Longest message of the host the endpoint accepts, in characters."""

WEEKDAYS_PL: Final = (
    "poniedziałek",
    "wtorek",
    "środa",
    "czwartek",
    "piątek",
    "sobota",
    "niedziela",
)
"""Weekday names for the calendar in the instructions, Monday first."""

CALENDAR_DAYS: Final = 14
"""How many days ahead the instructions list with their dates."""

STATE_CONFIDENCE_KEY: Final = "confidence"
"""Key of a decision model's confidence in ``ModelResponse.provider_details``."""

TRIP_FACT_FIELDS: Final = frozenset(
    {
        "destination",
        "start_date",
        "end_date",
        "currency",
        "budget_total_min",
        "budget_total_max",
        "budget_day_min",
        "budget_day_max",
        "budget_flex_pct",
    }
)
"""Trip fields the instructions show the model."""

ERROR_SPEND_PL: Final = (
    "Limit kosztów wywiadu dla tej podróży został wyczerpany. "
    "Uzupełnij dane w panelu „Co już wiem” albo wróć do wywiadu później."
)
"""Run error: the trip's interview budget is spent."""

ERROR_TIMEOUT_PL: Final = "Asystent nie zdążył odpowiedzieć. Spróbuj jeszcze raz."
"""Run error: the turn took longer than the configured limit."""

ERROR_UNAVAILABLE_PL: Final = "Asystent jest chwilowo niedostępny. Spróbuj za chwilę."
"""Run error: no model could be reached."""

ERROR_GENERIC_PL: Final = "Asystent napotkał błąd. Spróbuj jeszcze raz."
"""Run error: anything else; details go to the log, not to the client."""

ERROR_CODE_SPEND: Final = "spend_limit"
"""``RUN_ERROR`` code for a spent budget."""

ERROR_CODE_TIMEOUT: Final = "timeout"
"""``RUN_ERROR`` code for a turn that took too long."""

ERROR_CODE_UNAVAILABLE: Final = "unavailable"
"""``RUN_ERROR`` code for an unreachable model."""

ERROR_CODE_GENERIC: Final = "error"
"""``RUN_ERROR`` code for anything else."""

OPENAI_HANGUP_URL: Final = "https://api.openai.com/v1/realtime/calls/{call_id}/hangup"
"""OpenAI endpoint that ends a WebRTC call."""

OPENAI_KEY_ENV: Final = "OPENAI_API_KEY"
"""Environment variable Pydantic AI reads the OpenAI key from."""

HANGUP_TIMEOUT_SECONDS: Final = 5.0
"""How long the best-effort provider hangup may take."""

VOICE_INSTRUCTIONS: Final = (
    "This is a live voice call. Speak in short, natural sentences, ask one "
    "question at a time and do not read lists aloud. Do not use show_card: "
    "ask the question out loud instead."
)
"""Added to the interview instructions for the voice agent."""

GUARD_MARGIN_SECONDS: Final = 30.0
"""Slack added to a run's time limit before its claim counts as abandoned."""

INTERRUPTED_NOTE: Final = "(Odpowiedź asystenta została przerwana.)"
"""Closes a stored turn that failed half-way, so the next run can continue."""
