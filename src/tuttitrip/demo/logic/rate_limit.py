"""A small in-memory sliding-window rate limiter keyed by client.

Per process: with several API workers each has its own budget, so the demo
runs a single worker.
"""

from collections import OrderedDict, deque
from collections.abc import Callable
from time import monotonic

WINDOW_SECONDS = 60.0
MAX_KEYS = 10_000


class RateLimiter:
    """Allow at most ``limit`` hits per key in the last minute (per process)."""

    def __init__(
        self,
        limit: int,
        clock: Callable[[], float] = monotonic,
        max_keys: int = MAX_KEYS,
    ) -> None:
        """Create a limiter.

        Args:
            limit: Hits allowed per key per window.
            clock: Monotonic seconds, replaceable in tests.
            max_keys: Hard cap on remembered keys; the least recently used go first.
        """
        self.limit = limit
        self.max_keys = max_keys
        self._clock = clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def allow(self, key: str) -> bool:
        """Record a hit and say whether it is within the limit.

        Args:
            key: Who is calling (the client IP).

        Returns:
            False when the key already used its hits in the window.
        """
        now = self._clock()
        hits = self._hits.get(key)
        if hits is None:
            hits = self._hits[key] = deque()
            while len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)
        else:
            self._hits.move_to_end(key)
        while hits and now - hits[0] >= WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True
