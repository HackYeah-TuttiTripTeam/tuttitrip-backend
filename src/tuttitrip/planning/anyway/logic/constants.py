"""Fixed values of the "anyway" suggestion."""

from typing import Final

CANDIDATES_PER_DAY: Final = 1
"""Candidates tried per day of the trip. Each one costs one more group run of the
solver (the solo runs are reused), so the work is bounded by the number of days."""

TEMPLATE: Final = (
    "To miejsce nie weszło do planu, bo pasuje do grupy słabiej (opinia {v_p:+.2f}), "
    "ale jest kultowe lub wyjątkowe. Dodane do dnia {day} zmieni koszt o "
    "{d_cost:+.2f} {currency}, czas o {d_minutes:+d} min, a najniższy udział "
    "w maksimum (min r) o {d_min_r:+.3f}."
)
"""Fallback justification, written from the numbers when the model's text is not
there yet (docs/algorytm.md: only numbers the algorithm computed)."""

JOB_USER: Final = "anyway"
"""Owner recorded on the justification job when the host did not start it."""
