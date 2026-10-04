"""How much would an answer change the plan: the informativeness of a question.

For every missing field the plan is solved again with two or three plausible
answers and compared with the plan for what is known now. The score of a field
is the mean change over its answers, where one answer's change is

    ``HASH_WEIGHT`` if the plan differs (another ``plan_hash``)
    + ``min(1, |dJ| / 100)``  (the objective ``J`` of E5, in welfare points;
                              capped, because a missed guarantee costs 1000)
    + ``|d min u| / 100``  (the welfare of the worst-off person)

so a field that reshuffles the plan always outranks one that only nudges the
numbers. ``min u`` stands in for ``min r``: ``r`` needs a solo run per person
and per answer, which would not fit the time of one interview turn.

Pure and deterministic: the solver is, and every run has the same work limit in
evaluated plans (not in seconds), so the same input gives the same scores. The
time budget only decides whether the answer is used at all.
"""

import time
from collections.abc import Sequence
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid5

from tuttitrip.places.schemas import PlaceTag
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.solver import PlanResult, solve
from tuttitrip.planning.schemas import (
    PlanningInput,
    PlanningPerson,
    WhatIfField,
    WhatIfTarget,
)
from tuttitrip.profiles.preferences.schemas import (
    POOL_TOTAL,
    ImportanceDomain,
    ImportancePool,
)

PROBE_EVALUATIONS: Final = 200
"""Work limit of every run, in evaluated plans (equal for all, so comparable)."""

HASH_WEIGHT: Final = 1.0
"""Change counted when an answer gives another plan."""

POINT_SCALE: Final = 100.0
"""Welfare is in points of 0 to 100; differences are divided by this."""

SCORE_DIGITS: Final = 6
"""Scores are rounded, so equal changes tie and tie-breaks stay deterministic."""

DATE_LENGTHS: Final = (1, 3)
"""Trip lengths in days tried for the dates question."""

BUDGET_FACTORS: Final = (Decimal("0.5"), Decimal("1.5"))
"""Budget tried for the budget question, as a share of the current plan's cost."""

PACE_FACTORS: Final = (0.5, 1.5)
"""The slowest person's walking limits tried, as a share of the current ones."""

FOCUS_POINTS: Final = 6
"""Points a focused domain gets in the importance question; others share the rest."""

FOCUS_DOMAINS: Final = (ImportanceDomain.ATTRACTIONS, ImportanceDomain.FOOD)
"""Domains tried as the most important one."""

INTEREST_TAGS: Final = 2
"""How many of the catalog's most common tags the interests question tries."""

CHILD_AGE: Final = 7
"""Age of the child added in the people question."""

CHILD_WEIGHT: Final = 2.0
"""Vote weight of that child (the child weight of the spec)."""

CHILD_WALK_SHARE: Final = 0.5
"""A child walks this share of the first person's distances."""

CHILD_ACTIVE_SHARE: Final = 0.7
"""A child is active this share of the first person's minutes."""

_NAMESPACE: Final = UUID("5e1d3b7a-91c4-4a2e-8f60-2b7c9d4e1a35")


def _replace_person(
    data: PlanningInput, person: PlanningPerson, changed: PlanningPerson
) -> PlanningInput:
    people = tuple(changed if p.id == person.id else p for p in data.people)
    return data.model_copy(update={"people": people})


def _dates(data: PlanningInput) -> list[PlanningInput]:
    first = data.trip.days[0]
    lengths = [n for n in DATE_LENGTHS if n != len(data.trip.days)]
    return [
        data.model_copy(
            update={
                "trip": data.trip.model_copy(
                    update={
                        "days": tuple(
                            first.fromordinal(first.toordinal() + i) for i in range(n)
                        )
                    }
                )
            }
        )
        for n in lengths
    ]


def _people(data: PlanningInput) -> list[PlanningInput]:
    template = data.people[0]
    count = len(data.people)
    adult_id = uuid5(_NAMESPACE, f"adult-{count}")
    child_id = uuid5(_NAMESPACE, f"child-{count}")
    blank = {"votes": {}, "vetoes": frozenset(), "min_tags": (), "floor": 0.0}
    adult = template.model_copy(update={"id": adult_id, **blank})
    child = template.model_copy(
        update={
            "id": child_id,
            "age": CHILD_AGE,
            "weight": CHILD_WEIGHT,
            "segment_km": template.segment_km * CHILD_WALK_SHARE,
            "daily_km": template.daily_km * CHILD_WALK_SHARE,
            "active_min": max(1, round(template.active_min * CHILD_ACTIVE_SHARE)),
            **blank,
        }
    )
    return [
        data.model_copy(update={"people": (*data.people, extra)})
        for extra in (adult, child)
    ]


def _budget(data: PlanningInput, base: PlanResult) -> list[PlanningInput]:
    cost = base.cost.total
    if cost <= 0:
        return []
    out = []
    for factor in BUDGET_FACTORS:
        limit = (cost * factor).quantize(Decimal("0.01"))
        trip = data.trip.model_copy(
            update={
                "budget_from": min(data.trip.budget_from, limit),
                "budget_to": limit,
            }
        )
        out.append(data.model_copy(update={"trip": trip}))
    return out


def _pace(data: PlanningInput, person: PlanningPerson) -> list[PlanningInput]:
    return [
        _replace_person(
            data,
            person,
            person.model_copy(
                update={
                    "segment_km": person.segment_km * factor,
                    "daily_km": person.daily_km * factor,
                    "active_min": max(1, round(person.active_min * factor)),
                }
            ),
        )
        for factor in PACE_FACTORS
    ]


def _importance(data: PlanningInput, person: PlanningPerson) -> list[PlanningInput]:
    rest = (POOL_TOTAL - FOCUS_POINTS) // (len(ImportanceDomain) - 1)
    out = []
    for focus in FOCUS_DOMAINS:
        pool = ImportancePool.model_validate(
            {d.value: FOCUS_POINTS if d is focus else rest for d in ImportanceDomain}
        )
        out.append(
            _replace_person(data, person, person.model_copy(update={"pool": pool}))
        )
    return out


def _requirements(data: PlanningInput, person: PlanningPerson) -> list[PlanningInput]:
    strict = person.model_copy(update={"stairs_sensitivity": 1.0})
    return [_replace_person(data, person, strict)]


def _interests(data: PlanningInput, person: PlanningPerson) -> list[PlanningInput]:
    counts: dict[PlaceTag, int] = {}
    for place in data.places:
        for tag in place.tags:
            if tag not in person.interests:
                counts[tag] = counts.get(tag, 0) + 1
    common = sorted(counts, key=lambda t: (-counts[t], t.value))[:INTEREST_TAGS]
    return [
        _replace_person(
            data,
            person,
            person.model_copy(update={"interests": {**person.interests, t: 1.0}}),
        )
        for t in common
    ]


_PERSONAL: Final = {
    WhatIfField.PACE: _pace,
    WhatIfField.IMPORTANCE: _importance,
    WhatIfField.REQUIREMENTS: _requirements,
    WhatIfField.INTERESTS: _interests,
}


def answers(
    data: PlanningInput, base: PlanResult, target: WhatIfTarget
) -> list[PlanningInput]:
    """The plan input for each plausible answer to one question.

    Args:
        data: The input for what is known now.
        base: Its plan (the budget question is relative to its cost).
        target: The question; a personal one names the person.

    Returns:
        One input per answer; empty when nothing can be tried (a person that is
        not in the input, or a plan with no cost to scale a budget from).
    """
    if target.field is WhatIfField.DATES:
        return _dates(data)
    if target.field is WhatIfField.PEOPLE:
        return _people(data)
    if target.field is WhatIfField.BUDGET:
        return _budget(data, base)
    person = next((p for p in data.people if p.id == target.person_id), None)
    return _PERSONAL[target.field](data, person) if person is not None else []


def _worst_off(plan: PlanResult) -> float:
    return min(s.welfare for s in plan.scores)


def change(base: PlanResult, other: PlanResult) -> float:
    """How much another answer changes the plan (see the module docstring).

    Args:
        base: The plan for what is known now.
        other: The plan for one answer.

    Returns:
        A number from 0 up; at least ``HASH_WEIGHT`` when the plan differs.
    """
    return (
        (HASH_WEIGHT if other.plan_hash != base.plan_hash else 0.0)
        + min(1.0, abs(other.objective.value - base.objective.value) / POINT_SCALE)
        + abs(_worst_off(other) - _worst_off(base)) / POINT_SCALE
    )


def impacts(  # ruff: ignore[too-many-arguments] the whole input of a measurement
    data: PlanningInput,
    targets: Sequence[WhatIfTarget],
    params: AlgorithmParams = DEFAULT_PARAMS,
    *,
    alpha: float = 1.0,
    max_evaluations: int = PROBE_EVALUATIONS,
    budget_seconds: float | None = None,
) -> dict[WhatIfTarget, float] | None:
    """Score every question by how much its answers change the plan.

    Args:
        data: The input for what is known now (assumptions already applied).
        targets: The questions to measure.
        params: Algorithm parameters.
        alpha: The fairness slider.
        max_evaluations: Work limit of every run.
        budget_seconds: Give up (and return None) when the measurement takes
            longer; None: no limit.

    Returns:
        The score per question (0 when nothing could be tried), or None when the
        time ran out.
    """
    started = time.monotonic()

    def late() -> bool:
        return (
            budget_seconds is not None and time.monotonic() - started > budget_seconds
        )

    base = solve(data, params, alpha=alpha, max_evaluations=max_evaluations)
    scores: dict[WhatIfTarget, float] = {}
    for target in targets:
        changes: list[float] = []
        for variant in answers(data, base, target):
            if late():
                return None
            plan = solve(variant, params, alpha=alpha, max_evaluations=max_evaluations)
            changes.append(change(base, plan))
        scores[target] = (
            round(sum(changes) / len(changes), SCORE_DIGITS) if changes else 0.0
        )
    return None if late() else scores
