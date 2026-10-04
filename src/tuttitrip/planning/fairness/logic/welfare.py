"""Welfare of the group, ``W(P)`` (docs/algorytm.md, E5).

```
W(P) = sum_i w_i * phi_alpha(u_i)
phi_1(u) = ln(1 + u)
phi_alpha(u) = (1 + u)^(1 - alpha) / (1 - alpha)      (alpha != 1)
```

``alpha`` is continuous from 0 (utilitarian) through 1 (Nash, the default) to 3
(almost egalitarian). Every ``phi_alpha`` is concave, so moving utility from a
better-off to a worse-off person never lowers ``W`` (Pigou-Dalton). ``W`` is not
scaled to 0-100: the violation penalty (1000) must dominate its differences.
"""

import math
from collections.abc import Iterable

ALPHA_MIN = 0.0
ALPHA_MAX = 3.0
_NASH_TOLERANCE = 1e-9


def phi(utility: float, alpha: float = 1.0) -> float:
    """Value of one person's utility under inequality aversion ``alpha``.

    Args:
        utility: ``u_i`` in 0 to 100.
        alpha: Fairness slider in 0 to 3; 1 is Nash (the logarithm).

    Returns:
        ``ln(1 + u)`` for ``alpha = 1``, else ``(1 + u)^(1 - alpha) / (1 - alpha)``.

    Raises:
        ValueError: When ``alpha`` is outside 0 to 3.
    """
    if not ALPHA_MIN <= alpha <= ALPHA_MAX:
        msg = f"alpha must be in {ALPHA_MIN} to {ALPHA_MAX}, got {alpha}"
        raise ValueError(msg)
    if abs(alpha - 1) < _NASH_TOLERANCE:
        return math.log1p(utility)
    return math.pow(1 + utility, 1 - alpha) / (1 - alpha)


def welfare(people: Iterable[tuple[float, float]], alpha: float = 1.0) -> float:
    """``W(P)``: weighted sum of ``phi_alpha`` over people.

    ``math.fsum`` is exactly rounded, so the result does not depend on the order
    of the people.

    Args:
        people: ``(utility, weight)`` pairs, utility in 0-100.
        alpha: Fairness slider in 0 to 3.

    Returns:
        The group welfare.
    """
    return math.fsum(weight * phi(utility, alpha) for utility, weight in people)


def weighted_log_welfare(people: Iterable[tuple[float, float]]) -> float:
    """Sum ``w * log(1 + u)`` over people, i.e. ``W`` for ``alpha = 1``.

    The logarithm makes extra points worth less to someone who already has
    many, so the solver will not trade one person away for another.

    Args:
        people: ``(utility, weight)`` pairs, utility in 0-100.

    Returns:
        The objective value.
    """
    return welfare(people, 1.0)
