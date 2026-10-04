"""Brute force over small instances: the reference for the solver's local search."""

import itertools
from uuid import UUID

from tuttitrip.planning.logic.solver import Assignment, Evaluation, PlanEvaluator


def assignments(pool: list[UUID], days: int) -> list[Assignment]:
    """Every way to leave each place out or put it into one of the days."""
    result: list[Assignment] = []
    for slots in itertools.product(range(days + 1), repeat=len(pool)):
        result.append(  # ruff: ignore[manual-list-comprehension] readable nested comprehension is worse
            tuple(
                tuple(
                    sorted(
                        (p for p, s in zip(pool, slots, strict=True) if s == day + 1),
                        key=str,
                    )
                )
                for day in range(days)
            )
        )
    return result


def best(
    evaluator: PlanEvaluator, days: int, must: frozenset[UUID] = frozenset()
) -> Evaluation:
    """The feasible plan with the best key among all assignments.

    Args:
        evaluator: The evaluator of the instance (hard constraints and ``J``).
        days: Number of days of the trip.
        must: Places every considered plan has to contain.

    Returns:
        The best evaluation by the solver's own comparison key.
    """
    pool = [p.id for p in evaluator.candidates]
    found = [
        e
        for a in assignments(pool, days)
        if must <= {i for day in a for i in day} and (e := evaluator.evaluate(a))
    ]
    return min(found, key=lambda e: e.key)
