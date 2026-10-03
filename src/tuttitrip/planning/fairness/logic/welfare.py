"""Weighted log welfare, the solver's objective function."""

import math
from collections.abc import Iterable


def weighted_log_welfare(people: Iterable[tuple[float, float]]) -> float:
    """Sum ``w * log(1 + u)`` over people.

    The logarithm makes extra points worth less to someone who already has
    many, so the solver will not trade one person away for another.

    Args:
        people: ``(utility, weight)`` pairs, utility in 0-100.

    Returns:
        The objective value.
    """
    return math.fsum(weight * math.log1p(utility) for utility, weight in people)
