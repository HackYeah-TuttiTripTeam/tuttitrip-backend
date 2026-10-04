"""Reproducible plan hash (docs/algorytm.md, E5, sections 4, 8 and 10).

The spec calls the hash a label of the *plan* ("Etykieta powtarzalności planu")
and requires a group of ``n`` identical clones to get "exactly the same plan
(the same hash)" as one person (section 4, test 5). So the hash covers the plan
itself and nothing that depends on who or how many take part: the days, the
visits in time order (then id) with their times rounded to 5 minutes, and the
number of nights. It does not cover people, scores or amounts (the cost of ``n``
clones is ``n`` times that of one, and their rows are ``n`` times as many).

The hash is the first 12 hex characters of the SHA-256 of a canonical JSON with
sorted keys. Nothing here iterates a set, so it is the same in every process
(Python randomises string hashing per process).
"""

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from tuttitrip.planning.plans.logic.hashing import compute_plan_hash

TIME_STEP_MIN = 5
_MINUTES_PER_HOUR = 60


def _minutes(moment: datetime) -> int:
    # Minutes since local midnight rounded half up to the 5-minute step.
    total = moment.hour * _MINUTES_PER_HOUR + moment.minute + moment.second / 60
    return int(total / TIME_STEP_MIN + 0.5) * TIME_STEP_MIN


def canonical_plan(
    days: Sequence[tuple[date, Sequence[tuple[UUID, datetime, datetime]]]],
    nights: int = 0,
) -> dict[str, object]:
    """The plan as JSON-ready data in canonical order.

    Args:
        days: Per day its date and the visits as ``(place id, start, end)``.
        nights: Number of nights of the lodging base (0 without one).

    Returns:
        Content that depends only on the plan itself.
    """
    return {
        "days": [
            {
                "date": day.isoformat(),
                "visits": [
                    {"place": str(pid), "start": _minutes(start), "end": _minutes(end)}
                    for pid, start, end in sorted(
                        visits, key=lambda v: (_minutes(v[1]), str(v[0]))
                    )
                ],
            }
            for day, visits in days
        ],
        "nights": nights,
    }


def plan_hash(
    days: Sequence[tuple[date, Sequence[tuple[UUID, datetime, datetime]]]],
    nights: int = 0,
) -> str:
    """12-character hash of a plan.

    Args:
        days: Per day its date and the visits as ``(place id, start, end)``.
        nights: Number of nights of the lodging base (0 without one).

    Returns:
        The first 12 hex characters of the SHA-256 of the canonical JSON.
    """
    return compute_plan_hash(canonical_plan(days, nights))
