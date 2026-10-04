"""One run per interview session at a time.

Two runs on the same session would both append to the history they loaded, and
the second write would silently drop the first turn. The marker lives in this
process (the API is a single uvicorn process); a marker older than the TTL is
treated as abandoned, so a stream that never finished cannot lock a session.
"""

import time
from uuid import UUID

from tuttitrip.shared.config.settings import get_settings


class SessionBusyError(Exception):
    """Another run of this session is still in progress."""


_running: dict[UUID, float] = {}


def acquire(session_id: UUID) -> None:
    """Mark the session as running.

    Args:
        session_id: The interview session.

    Raises:
        SessionBusyError: A run started less than the TTL ago has not ended.
    """
    now = time.monotonic()
    started = _running.get(session_id)
    if (
        started is not None
        and now - started < get_settings().interview.run_lock_ttl_seconds
    ):
        raise SessionBusyError(str(session_id))
    _running[session_id] = now


def release(session_id: UUID) -> None:
    """Clear the marker (also when none is set).

    Args:
        session_id: The interview session.
    """
    _running.pop(session_id, None)
