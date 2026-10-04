"""What going over ``B_do`` buys, from the two stored plans. Pure.

The log entry of a consent holds the same measures as an override: the change of
``min r``, Jain's index, ``r`` per person, cost and active time, here of
``P_flex`` against ``P_strict``.
"""

from collections.abc import Sequence

from tuttitrip.planning.overrides.schemas import (
    BudgetConsent,
    DecisionEffects,
    PersonDelta,
)
from tuttitrip.planning.plans.schemas import PlanDay, PlanRead

MINUTES_PER_HOUR = 60


def _active_minutes(days: Sequence[PlanDay]) -> int:
    # Time spent at the stops; the legs between them are not part of it.
    return sum(
        (stop.end.hour - stop.start.hour) * MINUTES_PER_HOUR
        + stop.end.minute
        - stop.start.minute
        for day in days
        for stop in day.items
    )


def consent_effects(
    flex: PlanRead, strict: PlanRead, consent: BudgetConsent
) -> DecisionEffects:
    """The effects of ``P_flex`` over ``P_strict``.

    Args:
        flex: The plan over ``B_do``.
        strict: The plan within ``B_do``.
        consent: The facts of the consent, kept with the numbers.

    Returns:
        ``flex`` minus ``strict``, with the consent attached.
    """
    before = {p.profile_id: p.r for p in strict.fairness.per_person}
    return DecisionEffects(
        d_min_r=flex.fairness.min_r - strict.fairness.min_r,
        d_jain=flex.fairness.jain - strict.fairness.jain,
        d_r=[
            PersonDelta(
                profile_id=p.profile_id, d_r=p.r - before.get(p.profile_id, p.r)
            )
            for p in flex.fairness.per_person
        ],
        d_cost=flex.budget.cost - strict.budget.cost,
        d_minutes=_active_minutes(flex.days) - _active_minutes(strict.days),
        budget=consent,
    )
