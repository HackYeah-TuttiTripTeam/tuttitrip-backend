"""Interview queries on PostgreSQL."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.interview import constants
from tuttitrip.interview.models import AssistantValue, InterviewSession
from tuttitrip.interview.schemas import FieldRef, SessionStatus


async def select_open_session(
    session: AsyncSession,
    trip_id: UUID,
    *,
    profile_id: UUID | None = None,
    lock: bool = False,
) -> InterviewSession | None:
    """The open interview of a trip, or of one member of it.

    Args:
        session: Open session.
        trip_id: Trip id.
        profile_id: The member's profile; None for the trip's own interview.
        lock: Lock the row until the transaction ends (for appends).

    Returns:
        The session, or None when there is none open.
    """
    stmt = select(InterviewSession).where(
        InterviewSession.trip_id == trip_id,
        InterviewSession.status == SessionStatus.OPEN,
        InterviewSession.profile_id.is_(None)
        if profile_id is None
        else InterviewSession.profile_id == profile_id,
    )
    return await session.scalar(stmt.with_for_update() if lock else stmt)


async def select_session(
    session: AsyncSession, trip_id: UUID, session_id: UUID, *, lock: bool = False
) -> InterviewSession | None:
    """One session of a trip.

    Args:
        session: Open session.
        trip_id: Trip id (a session of another trip does not match).
        session_id: Session id, the AG-UI thread id.
        lock: Lock the row until the transaction ends (for appends).

    Returns:
        The session, or None.
    """
    stmt = select(InterviewSession).where(
        InterviewSession.id == session_id, InterviewSession.trip_id == trip_id
    )
    return await session.scalar(stmt.with_for_update() if lock else stmt)


async def insert_open_session(
    session: AsyncSession,
    trip_id: UUID,
    created_by: str,
    profile_id: UUID | None = None,
) -> InterviewSession | None:
    """Open a session unless there is already one (race-safe).

    Args:
        session: Open session.
        trip_id: Trip id.
        created_by: Auth0 subject of the caller.
        profile_id: The member's profile; None for the trip's own interview.

    Returns:
        The new session, or None when an open one already exists.
    """
    is_open = InterviewSession.status == SessionStatus.OPEN
    if profile_id is None:
        target = [InterviewSession.trip_id]
        where = is_open & InterviewSession.profile_id.is_(None)
    else:
        target = [InterviewSession.profile_id]
        where = is_open & InterviewSession.profile_id.is_not(None)
    stmt = (
        insert(InterviewSession)
        .values(
            trip_id=trip_id,
            created_by=created_by,
            profile_id=profile_id,
            history=[],
        )
        .on_conflict_do_nothing(index_elements=target, index_where=where)
        .returning(InterviewSession)
    )
    return await session.scalar(stmt, execution_options={"populate_existing": True})


async def select_assistant_digests(
    session: AsyncSession, trip_id: UUID
) -> dict[FieldRef, str]:
    """Digests of the values the assistant wrote on a trip.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        Digest per value.
    """
    rows = await session.scalars(
        select(AssistantValue).where(AssistantValue.trip_id == trip_id)
    )
    return {
        FieldRef(field=row.field, profile_id=row.profile_id): row.digest for row in rows
    }


async def upsert_assistant_digest(
    session: AsyncSession, trip_id: UUID, ref: FieldRef, digest: str
) -> None:
    """Remember that the assistant wrote a value.

    Args:
        session: Open session.
        trip_id: Trip id.
        ref: Which value.
        digest: Digest of the value as it is stored now.
    """
    stmt = insert(AssistantValue).values(
        trip_id=trip_id, field=ref.field, profile_id=ref.profile_id, digest=digest
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[
                AssistantValue.trip_id,
                AssistantValue.field,
                AssistantValue.profile_id,
            ],
            set_={"digest": digest, "updated_at": func.now()},
        )
    )


async def try_start_run(
    session: AsyncSession,
    trip_id: UUID,
    session_id: UUID,
    ttl_seconds: float,
    voice_limit: int | None,
) -> datetime | None:
    """Claim the session for one run; atomic, so two callers cannot both win.

    Args:
        session: Open session (the claim is committed).
        trip_id: Trip id (a session of another trip does not match).
        session_id: Session id.
        ttl_seconds: After this long the claim counts as abandoned.
        voice_limit: For a voice call, the seconds the interview may use in all.

    Returns:
        The claim's expiry (the token for ``end_run``), or None when the session
        is missing, busy or out of voice time.
    """
    kind = constants.RUN_TEXT if voice_limit is None else constants.RUN_VOICE
    now = func.now()
    free = or_(
        InterviewSession.running_until.is_(None), InterviewSession.running_until < now
    )
    conditions = [
        InterviewSession.id == session_id,
        InterviewSession.trip_id == trip_id,
        free,
    ]
    if voice_limit is not None:
        conditions.append(InterviewSession.voice_seconds < voice_limit)
    stmt = (
        update(InterviewSession)
        .where(*conditions)
        .values(
            running_until=now + func.make_interval(0, 0, 0, 0, 0, 0, ttl_seconds),
            running_kind=kind,
        )
        .returning(InterviewSession.running_until)
    )
    claimed = await session.scalar(stmt)
    await session.commit()
    return claimed


async def end_run(
    session: AsyncSession, session_id: UUID, token: datetime, voice_seconds: int
) -> None:
    """Release a claim and book the voice time it used.

    The release is skipped when the claim expired and someone else took the
    session since; the voice time is booked either way.

    Args:
        session: Open session (committed).
        session_id: Session id.
        token: The expiry ``try_start_run`` returned.
        voice_seconds: Seconds of voice to add (0 for a text turn).
    """
    await session.execute(
        update(InterviewSession)
        .where(
            InterviewSession.id == session_id,
            InterviewSession.running_until == token,
        )
        .values(running_until=None, running_kind=None)
    )
    if voice_seconds:
        await session.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session_id)
            .values(voice_seconds=InterviewSession.voice_seconds + voice_seconds)
        )
    await session.commit()


async def extend_run(
    session: AsyncSession, session_id: UUID, token: datetime, ttl_seconds: float
) -> datetime | None:
    """Push the expiry of a claim forward (the heartbeat of a live call).

    Args:
        session: Open session (committed).
        session_id: Session id.
        token: The current expiry of the claim.
        ttl_seconds: The claim lasts this long from now.

    Returns:
        The new expiry (the new token), or None when the claim is gone: it
        expired and someone else took the session, or it was released.
    """
    stmt = (
        update(InterviewSession)
        .where(
            InterviewSession.id == session_id,
            InterviewSession.running_until == token,
        )
        .values(
            running_until=func.now() + func.make_interval(0, 0, 0, 0, 0, 0, ttl_seconds)
        )
        .returning(InterviewSession.running_until)
    )
    extended = await session.scalar(stmt)
    await session.commit()
    return extended


async def clear_voice_run(
    session: AsyncSession, trip_id: UUID, session_id: UUID
) -> bool:
    """Free a session that a voice call holds, whatever claim holds it.

    A text turn is left alone: its stream ends by itself within its time limit.

    Args:
        session: Open session (committed).
        trip_id: Trip id (a session of another trip does not match).
        session_id: Session id.

    Returns:
        Whether a voice claim was cleared.
    """
    cleared = await session.scalar(
        update(InterviewSession)
        .where(
            InterviewSession.id == session_id,
            InterviewSession.trip_id == trip_id,
            InterviewSession.running_kind == constants.RUN_VOICE,
        )
        .values(running_until=None, running_kind=None)
        .returning(InterviewSession.id)
    )
    await session.commit()
    return cleared is not None
