"""One run per interview session at a time, text or voice, across processes.

Two runs on the same session would both append to the history they loaded, and
the second write would silently drop the first turn. The claim is one atomic
``UPDATE`` on the session row (``db.try_start_run``): it succeeds only when no
run holds the session or the holder's claim has expired, so a crashed run cannot
lock the session for longer than its time limit plus a margin. The claim is
released in a ``finally`` around the whole stream and again by a background task
of the response (releasing twice is harmless: the token no longer matches).
"""

import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import anyio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tuttitrip.interview import constants, db
from tuttitrip.interview.services.session_service import SessionNotFoundError
from tuttitrip.trips.schemas import TripMembership


class SessionBusyError(Exception):
    """Another run (a text turn or a voice call) holds this session."""


class VoiceBudgetError(Exception):
    """The interview has used all the voice time its trip may use."""


@dataclass(frozen=True)
class Claim:
    """A held session; give it back with ``release``."""

    session_id: UUID
    token: datetime
    sessions: async_sessionmaker[AsyncSession]
    started: float = field(default_factory=time.monotonic)


async def acquire(
    sessions: async_sessionmaker[AsyncSession],
    membership: TripMembership,
    session_id: UUID,
    *,
    limit_seconds: float,
    voice_limit: int | None = None,
) -> Claim:
    """Claim the session for a run.

    Args:
        sessions: Session factory (the claim has its own transactions).
        membership: The caller's checked membership (binds the session to the trip).
        session_id: The interview session.
        limit_seconds: The run's time limit; the claim expires a margin later.
        voice_limit: For a voice call, the voice seconds the interview may use in all.

    Returns:
        The claim.

    Raises:
        SessionNotFoundError: No such session on this trip.
        SessionBusyError: A run holds it.
        VoiceBudgetError: A voice call, but the voice time is used up.
    """
    ttl = limit_seconds + constants.GUARD_MARGIN_SECONDS
    async with sessions() as session:
        token = await db.try_start_run(
            session, membership.trip_id, session_id, ttl, voice_limit
        )
    if token is not None:
        return Claim(session_id=session_id, token=token, sessions=sessions)
    async with sessions() as session:
        row = await db.select_session(session, membership.trip_id, session_id)
    if row is None:
        raise SessionNotFoundError(str(session_id))
    if voice_limit is not None and row.voice_seconds >= voice_limit:
        raise VoiceBudgetError(str(session_id))
    raise SessionBusyError(str(session_id))


async def release(claim: Claim, *, voice: bool = False) -> None:
    """Give the session back; safe to call twice and when cancelled.

    Args:
        claim: What ``acquire`` returned.
        voice: Book the time since the claim as voice time.
    """
    seconds = math.ceil(time.monotonic() - claim.started) if voice else 0
    with anyio.CancelScope(shield=True):
        async with claim.sessions() as session:
            await db.end_run(session, claim.session_id, claim.token, seconds)
