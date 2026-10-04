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
    # Integer shares in proportion (even when the others are all zero); the
    # points left over go to the largest remainders, ties to the larger old
    # value and then to the earlier domain.
    scaled = {d: share * (v if weight else 1) for d, v in others.items()}
    base = weight or len(others)
    out = {d: n // base for d, n in scaled.items()}
    order = sorted(
        scaled,
        key=lambda d: (scaled[d] % base, others[d], -list(scaled).index(d)),
        reverse=True,
    )
    for d in order[: share - sum(out.values())]:
        out[d] += 1
    return {d: points if d == domain else out[d] for d in pool}
