"""Interview sessions: resume, history storage and the "What we already know" view.

The history is stored as JSONB through ``ModelMessagesTypeAdapter``. It is
written only by the server (the AG-UI endpoint passes the stored history to the
adapter as ``message_history``); what a client sends is never saved as truth.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    TextPart,
    UserPromptPart,
)
from pydantic_core import to_jsonable_python
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.interview import db
from tuttitrip.interview.logic import knowledge
from tuttitrip.interview.models import InterviewSession
from tuttitrip.interview.schemas import (
    DisplayMessage,
    FieldRef,
    InterviewSessionRead,
    KnowledgeRead,
    MessageRole,
    MessagesQuery,
    SessionRead,
)
from tuttitrip.profiles.preferences.schemas import PreferencesRead
from tuttitrip.profiles.preferences.services import preference_service
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.schemas import TripMembership, TripRead
from tuttitrip.trips.services import trip_service


class SessionNotFoundError(Exception):
    """The trip has no such (open) interview session."""


class UnknownFieldError(Exception):
    """A value cannot be marked: it is not filled or not on this trip."""


def _display(history: Sequence[ModelMessage]) -> list[DisplayMessage]:
    shown: list[DisplayMessage] = []

    def add(role: MessageRole, text: str, stamp: datetime | None) -> None:
        if text.strip():
            shown.append(
                DisplayMessage(
                    position=len(shown), role=role, text=text, timestamp=stamp
                )
            )

    for message in history:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    content = part.content
                    text = (
                        content
                        if isinstance(content, str)
                        else " ".join(c for c in content if isinstance(c, str))
                    )
                    add(MessageRole.USER, text, part.timestamp)
        else:
            text = "".join(p.content for p in message.parts if isinstance(p, TextPart))
            add(MessageRole.ASSISTANT, text, message.timestamp)
    return shown


def _history(row: InterviewSession) -> list[ModelMessage]:
    return ModelMessagesTypeAdapter.validate_python(row.history)


def _read(row: InterviewSession, shown: int) -> SessionRead:
    return SessionRead(
        id=row.id,
        trip_id=row.trip_id,
        status=row.status,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        message_count=shown,
    )


async def open_session(
    session: AsyncSession, membership: TripMembership
) -> tuple[SessionRead, bool]:
    """Create the trip's interview session, or return the open one.

    Safe to call twice and from two requests at once: a partial unique index
    allows one open session per trip.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.

    Returns:
        The session and whether this call created it.
    """
    row = await db.insert_open_session(session, membership.trip_id, membership.sub)
    created = row is not None
    if row is None:
        row = await db.select_open_session(session, membership.trip_id)
    if row is None:  # closed between the two statements; vanishingly rare
        raise SessionNotFoundError(str(membership.trip_id))
    await session.commit()
    return _read(row, len(_display(_history(row)))), created


async def get_current(
    session: AsyncSession, membership: TripMembership, query: MessagesQuery
) -> InterviewSessionRead:
    """Read the open session with one page of its conversation.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        query: Page, size, direction and the speaker filter.

    Returns:
        The session and the messages the host can see.

    Raises:
        SessionNotFoundError: The trip has no open session.
    """
    row = await db.select_open_session(session, membership.trip_id)
    if row is None:
        raise SessionNotFoundError(str(membership.trip_id))
    shown = _display(_history(row))
    wanted = [m for m in shown if query.role in {None, m.role}]
    if query.dir == "desc":
        wanted.reverse()
    page = Page[DisplayMessage].of(
        wanted[query.offset : query.offset + query.size], len(wanted), query
    )
    return InterviewSessionRead(**_read(row, len(shown)).model_dump(), messages=page)


async def load_history(
    session: AsyncSession, membership: TripMembership, session_id: UUID
) -> list[ModelMessage]:
    """The trusted history to pass as ``message_history`` to the agent.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        session_id: The AG-UI ``threadId``.

    Returns:
        All stored messages, tool calls included.

    Raises:
        SessionNotFoundError: No such session on this trip.
    """
    row = await db.select_session(session, membership.trip_id, session_id)
    if row is None:
        raise SessionNotFoundError(str(session_id))
    return _history(row)


async def append_messages(
    session: AsyncSession,
    membership: TripMembership,
    session_id: UUID,
    messages: Sequence[ModelMessage],
) -> None:
    """Add the messages of a finished run to the stored history.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        session_id: The AG-UI ``threadId``.
        messages: New messages only (``result.new_messages()``).

    Raises:
        SessionNotFoundError: No such session on this trip.
    """
    row = await db.select_session(session, membership.trip_id, session_id, lock=True)
    if row is None:
        raise SessionNotFoundError(str(session_id))
    row.history = [*row.history, *to_jsonable_python(list(messages))]
    await session.commit()


async def get_knowledge(
    session: AsyncSession, membership: TripMembership
) -> KnowledgeRead:
    """Build the "What we already know" view from ``trips`` and ``profiles``.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.

    Returns:
        The data, what is missing and who set each value.
    """
    trip, people, preferences = await _domain_data(session, membership)
    current = knowledge.values(trip, people, preferences)
    stored = await db.select_assistant_digests(session, membership.trip_id)
    return KnowledgeRead(
        trip=trip,
        people=people,
        preferences=preferences,
        missing=knowledge.missing(current),
        sources=knowledge.sources(current, stored),
    )


async def mark_assistant_values(
    session: AsyncSession, membership: TripMembership, refs: Sequence[FieldRef]
) -> None:
    """Record that the assistant just wrote these values.

    Call it right after the agent tool saved them through the ``trips`` or
    ``profiles`` services. If the host edits a value afterwards, its source
    turns into ``host`` by itself.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        refs: The values the assistant set.

    Raises:
        UnknownFieldError: A value is not filled or not on this trip.
    """
    trip, people, preferences = await _domain_data(session, membership)
    current = knowledge.values(trip, people, preferences)
    for ref in refs:
        value = current.get(ref)
        if value is None or not value.filled:
            raise UnknownFieldError(ref.model_dump_json())
        await db.upsert_assistant_digest(session, membership.trip_id, ref, value.digest)
    await session.commit()


async def _domain_data(
    session: AsyncSession, membership: TripMembership
) -> tuple[TripRead, list[ProfileRead], list[PreferencesRead]]:
    trip = await trip_service.get_trip(session, membership)
    people = await profile_service.list_profiles(session, membership)
    preferences = await preference_service.list_preferences(session, membership)
    return trip, people, preferences
