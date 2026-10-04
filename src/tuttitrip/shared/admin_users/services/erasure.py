"""Registry of the cleanups that run when an account is deleted.

``shared`` cannot import feature domains, so each domain registers its eraser
at the composition root (``tuttitrip.main``). An eraser flushes and never
commits: the caller wraps all of them, the block and the audit in one commit.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

Counts = dict[str, int]
Eraser = Callable[[AsyncSession, str], Awaitable[Counts]]

_ERASERS: list[Eraser] = []


def register(eraser: Eraser) -> None:
    """Add a cleanup (idempotent).

    Args:
        eraser: ``await eraser(session, sub)`` clears one domain's data and
            returns what it did as counts (they go to the audit entry).
    """
    if eraser not in _ERASERS:
        _ERASERS.append(eraser)


async def erase(session: AsyncSession, sub: str) -> Counts:
    """Run every registered cleanup for the account.

    Args:
        session: Open session (caller commits).
        sub: Auth0 subject of the deleted account.

    Returns:
        The counts of all cleanups together.
    """
    counts: Counts = {}
    for eraser in _ERASERS:
        counts |= await eraser(session, sub)
    return counts
