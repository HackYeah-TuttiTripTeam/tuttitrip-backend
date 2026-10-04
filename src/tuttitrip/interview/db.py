"""Interview queries on PostgreSQL."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.interview.models import AssistantValue, InterviewSession
from tuttitrip.interview.schemas import FieldRef, SessionStatus


async def select_open_session(
    session: AsyncSession, trip_id: UUID, *, lock: bool = False
) -> InterviewSession | None:
    """The open interview of a trip.

    Args:
        session: Open session.
        trip_id: Trip id.
        lock: Lock the row until the transaction ends (for appends).

    Returns:
        The session, or None when the trip has none open.
    """
    stmt = select(InterviewSession).where(
        InterviewSession.trip_id == trip_id,
        InterviewSession.status == SessionStatus.OPEN,
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
    session: AsyncSession, trip_id: UUID, created_by: str
) -> InterviewSession | None:
    """Open a session unless the trip already has one (race-safe).

    Args:
        session: Open session.
        trip_id: Trip id.
        created_by: Auth0 subject of the caller.

    Returns:
        The new session, or None when an open one already exists.
    """
    stmt = (
        insert(InterviewSession)
        .values(trip_id=trip_id, created_by=created_by, history=[])
        .on_conflict_do_nothing(
            index_elements=[InterviewSession.trip_id],
            index_where=InterviewSession.status == SessionStatus.OPEN,
        )
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
