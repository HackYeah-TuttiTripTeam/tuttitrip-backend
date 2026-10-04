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
from tuttitrip.interview.services.session_service import (
    SessionNotFoundError,
    busy_kind,
)
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.trips.schemas import TripMembership


class SessionBusyError(Exception):
    """Another run (a text turn or a voice call) holds this session."""

    def __init__(self, session_id: str, kind: str | None = None) -> None:
        """Remember what holds the session.

        Args:
            session_id: The busy session.
            kind: ``text`` or ``voice``, when known.
        """
        super().__init__(session_id)
        self.kind = kind


class VoiceBudgetError(Exception):
    """The interview has used all the voice time its trip may use."""


@dataclass
class Claim:
    """A held session; give it back with ``release``.

    ``token`` changes when the claim is extended (``touch``).
    """

    session_id: UUID
    token: datetime
    sessions: async_sessionmaker[AsyncSession]
    ttl_seconds: float = 0.0
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
    # A voice call lives on a short claim that its heartbeat keeps alive, so a dead
    # call frees the session fast; a text turn lives for its time limit plus a margin.
    ttl = (
        get_settings().interview.voice_claim_ttl_seconds
        if voice_limit is not None
        else limit_seconds + constants.GUARD_MARGIN_SECONDS
    )
    async with sessions() as session:
        token = await db.try_start_run(
            session, membership.trip_id, session_id, ttl, voice_limit
        )
    if token is not None:
        return Claim(
            session_id=session_id, token=token, sessions=sessions, ttl_seconds=ttl
        )
    async with sessions() as session:
        row = await db.select_session(session, membership.trip_id, session_id)
    if row is None:
        raise SessionNotFoundError(str(session_id))
    if voice_limit is not None and row.voice_seconds >= voice_limit:
        raise VoiceBudgetError(str(session_id))
    raise SessionBusyError(str(session_id), busy_kind(row))


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


async def touch(claim: Claim) -> bool:
    """Extend the claim of a live run; the heartbeat of a voice call.

    Args:
        claim: What ``acquire`` returned.

    Returns:
        False when the claim is gone (expired and taken, or released), so the
        run should stop.
    """
    async with claim.sessions() as session:
        token = await db.extend_run(
            session, claim.session_id, claim.token, claim.ttl_seconds
        )
    if token is None:
        return False
    claim.token = token
    return True


async def release_voice(
    sessions: async_sessionmaker[AsyncSession],
    membership: TripMembership,
    session_id: UUID,
) -> bool:
    """Free a session a voice call holds, whichever process holds the call.

    Args:
        sessions: Session factory.
        membership: The caller's checked membership (binds the session to the trip).
        session_id: The interview session.

    Returns:
        Whether a voice claim was cleared.
    """
    async with sessions() as session:
        return await db.clear_voice_run(session, membership.trip_id, session_id)
