"""Fairness metrics."""


def jain_index(values: list[float]) -> float:
    """Jain's fairness index ``(sum x)^2 / (n * sum x^2)``.

    Args:
        values: Non-negative shares, at least one.

    Returns:
        A value in (0, 1]; 1 for a single value or all equal.
    """
    total = sum(values)
    squares = sum(v * v for v in values)
    return 1.0 if squares == 0 else total * total / (len(values) * squares)
