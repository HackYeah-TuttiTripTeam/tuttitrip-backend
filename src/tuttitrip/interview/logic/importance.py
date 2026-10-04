"""Moving points in the importance pool, which always adds up to ten."""

from collections.abc import Mapping

from tuttitrip.profiles.preferences.schemas import POOL_TOTAL


def set_points(
    pool: Mapping[str, int], domain: str, points: int, total: int = POOL_TOTAL
) -> dict[str, int]:
    """Give one domain ``points`` and spread the rest over the others.

    The other domains keep their proportions (largest remainders, ties to the
    domain that has more points, then to the earlier one), so the pool still
    adds up to ``total``. With ``points`` equal to ``total`` the others drop to
    zero.

    Args:
        pool: Current points per domain; it adds up to ``total``.
        domain: The domain to change; a key of ``pool``.
        points: Its new points.
        total: The sum the pool keeps.

    Returns:
        The new pool.

    Raises:
        ValueError: ``points`` is negative or above ``total``, or ``domain`` is unknown.
    """
    if domain not in pool:
        msg = f"Unknown domain {domain!r}"
        raise ValueError(msg)
    if not 0 <= points <= total:
        msg = f"A domain gets 0 to {total} points, not {points}"
        raise ValueError(msg)
    others = {d: v for d, v in pool.items() if d != domain}
    share = total - points
    weight = sum(others.values())
    # Proportional (or even, when the others are all zero) with largest remainders.
    raw = {
        d: share * (v / weight if weight else 1 / len(others))
        for d, v in others.items()
    }
    out = {d: int(r) for d, r in raw.items()}
    leftover = share - sum(out.values())
    order = sorted(
        raw,
        key=lambda d: (raw[d] - out[d], others[d], -list(raw).index(d)),
        reverse=True,
    )
    for d in order[:leftover]:
        out[d] += 1
    return {d: points if d == domain else out[d] for d in pool}
