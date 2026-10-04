"""Reproducible plan hash (docs/algorytm.md, E5 and section 8).

The hash is the first 12 hex characters of the SHA-256 of a canonical JSON:
sorted keys, people sorted by id, visits in time order (then id), times rounded
to 5 minutes, money as ``Decimal`` text and scores rounded to four places. The
same plan gives the same hash in every process, whatever the order of the input
(Python randomises string hashing per process, so nothing here iterates a set).
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from tuttitrip.planning.plans.logic.hashing import compute_plan_hash
from tuttitrip.planning.schemas import DomainScores

TIME_STEP_MIN = 5
_MINUTES_PER_HOUR = 60


def _minutes(moment: datetime) -> int:
    # Minutes since local midnight rounded half up to the 5-minute step.
    total = moment.hour * _MINUTES_PER_HOUR + moment.minute + moment.second / 60
    return int(total / TIME_STEP_MIN + 0.5) * TIME_STEP_MIN


def canonical_plan(
    days: Sequence[tuple[date, Sequence[tuple[UUID, datetime, datetime]]]],
    scores: Sequence[DomainScores],
    cost: Decimal,
    lodging: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """The plan as JSON-ready data in canonical order.

    Args:
        days: Per day its date and the visits as ``(place id, start, end)``.
        scores: ``DomainScores`` of every person.
        cost: ``c(P)``.
        lodging: The lodging base as JSON-ready data, or None.

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
        "people": [
            {
                "id": str(s.person_id),
                "u": s.welfare,
                "q": {
                    "lodging": s.lodging,
                    "food": s.food,
                    "attractions": s.attractions,
                    "pace": s.pace,
                    "cost": s.cost,
                },
            }
            for s in sorted(scores, key=lambda s: str(s.person_id))
        ],
        "cost": str(cost),
        "lodging": None if lodging is None else dict(lodging),
    }


def plan_hash(
    days: Sequence[tuple[date, Sequence[tuple[UUID, datetime, datetime]]]],
    scores: Sequence[DomainScores],
    cost: Decimal,
    lodging: Mapping[str, object] | None = None,
) -> str:
    """12-character hash of a plan.

    Args:
        days: Per day its date and the visits as ``(place id, start, end)``.
        scores: ``DomainScores`` of every person.
        cost: ``c(P)``.
        lodging: The lodging base as JSON-ready data, or None.

    Returns:
        The first 12 hex characters of the SHA-256 of the canonical JSON.
    """
    return compute_plan_hash(canonical_plan(days, scores, cost, lodging))
