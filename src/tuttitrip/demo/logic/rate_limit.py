"""A small in-memory sliding-window rate limiter keyed by client."""

from collections import defaultdict, deque
from collections.abc import Callable
from time import monotonic

WINDOW_SECONDS = 60.0
MAX_KEYS = 10_000


class RateLimiter:
    """Allow at most ``limit`` hits per key in the last minute (per process)."""

    def __init__(self, limit: int, clock: Callable[[], float] = monotonic) -> None:
        """Create a limiter.

        Args:
            limit: Hits allowed per key per window.
            clock: Monotonic seconds, replaceable in tests.
        """
        self.limit = limit
        self._clock = clock
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """Record a hit and say whether it is within the limit.

        Args:
            key: Who is calling (the client IP).

        Returns:
            False when the key already used its hits in the window.
        """
        now = self._clock()
        if len(self._hits) >= MAX_KEYS and key not in self._hits:
            self._forget_idle(now)
        hits = self._hits[key]
        while hits and now - hits[0] >= WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def _forget_idle(self, now: float) -> None:
        idle = [
            k for k, h in self._hits.items() if not h or now - h[-1] >= WINDOW_SECONDS
        ]
        for key in idle:
            del self._hits[key]
