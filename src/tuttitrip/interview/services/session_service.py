"""Interview sessions: resume, history storage and the "What we already know" view.

Contract for the AG-UI endpoint (#56). The history is stored as JSONB through
``ModelMessagesTypeAdapter`` and is written only by the server:

- The only thing the client sends is the host's last text. The endpoint passes
  it to the agent as ``user_prompt`` and the stored history as
  ``message_history`` (``load_history``), never the client's ``messages`` or
  ``state``. pydantic-ai's AG-UI adapter would build
  ``[*server_history, *frontend_messages]`` and treat a trailing client request
  as resumed, leaving the host's turn out of ``new_messages()``.
- After the run, ``append_messages(result.new_messages())`` stores that user
  turn, the tool calls and the answer.
- One run per session at a time: the endpoint must reject (409) or serialise a
  second run on the same session, or both would append from the same base.
- Do not store a ``SystemPromptPart`` from a run and do not rely on a stored
  one: ``Agent`` instructions are applied per run, and a stored system prompt
  would go stale when the instructions change.
- ``GET .../sessions/current`` takes ``dir=desc`` to show the newest messages
  first (the chat UI reads page 1 of ``desc`` and scrolls up).
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    TextPart,
    UserPromptPart,
)
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.interview import constants, db
from tuttitrip.interview.logic import knowledge
from tuttitrip.interview.models import InterviewSession
from tuttitrip.interview.schemas import (
    DisplayMessage,
    FieldRef,
    InterviewSessionRead,
    KnowledgeField,
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
from tuttitrip.trips.schemas import TripMembership, TripRead, TripRole
from tuttitrip.trips.services import trip_service


class SessionNotFoundError(Exception):
    """The trip has no such (open) interview session."""


class HistoryIncompatibleError(Exception):
    """The stored history no longer validates (e.g. after a pydantic-ai upgrade)."""


class NoProfileError(Exception):
    """A member has no profile on this trip, so there is nobody to interview."""


class UnknownFieldError(Exception):
    """A value cannot be marked: it is not filled or not on this trip."""


async def owner_profile(
    session: AsyncSession, membership: TripMembership
) -> UUID | None:
    """Whose interview this is: the trip's (co-host and above) or a member's own.

    The role decides, not anything the client says. A co-host or host talks about
    the whole trip; a member talks only about themselves, through their own
    session that nobody else can read.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.

    Returns:
        None for the trip's interview, else the member's profile id.

    Raises:
        NoProfileError: A member without a profile on this trip.
    """
    if membership.role.satisfies(TripRole.CO_HOST):
        return None
    profile_id = await profile_service.find_account_profile(
        session, membership.trip_id, membership.sub
    )
    if profile_id is None:
        raise NoProfileError(membership.sub)
    return profile_id


def _display(history: Sequence[ModelMessage]) -> list[DisplayMessage]:
    shown: list[DisplayMessage] = []

    def add(role: MessageRole, text: str, stamp: datetime | None) -> None:
        if not text.strip():
            return
        if shown and shown[-1].role is role is MessageRole.ASSISTANT:
            # Text before and after a tool call is one answer for the host.
            last = shown[-1]
            shown[-1] = last.model_copy(update={"text": f"{last.text}\n\n{text}"})
        else:
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
    try:
        return ModelMessagesTypeAdapter.validate_python(row.history)
    except ValidationError as exc:
        msg = f"Stored history of session {row.id} cannot be read"
        raise HistoryIncompatibleError(msg) from exc


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
    owner = await owner_profile(session, membership)
    row = await db.insert_open_session(
        session, membership.trip_id, membership.sub, owner
    )
    created = row is not None
    if row is None:
        row = await db.select_open_session(
            session, membership.trip_id, profile_id=owner
        )
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
    owner = await owner_profile(session, membership)
    row = await db.select_open_session(session, membership.trip_id, profile_id=owner)
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


async def check_owned(
    session: AsyncSession, membership: TripMembership, session_id: UUID
) -> UUID | None:
    """Check that the session is the caller's before anything is claimed or read.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        session_id: The AG-UI ``threadId``.

    Returns:
        The member's profile id for a member's session, None for the trip's.

    Raises:
        SessionNotFoundError: No such session of the caller on this trip.
        NoProfileError: A member without a profile on this trip.
    """
    owner = await owner_profile(session, membership)
    row = await db.select_session(session, membership.trip_id, session_id)
    if row is None or row.profile_id != owner:
        raise SessionNotFoundError(str(session_id))
    return owner


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
        HistoryIncompatibleError: The stored history no longer validates.
    """
    owner = await owner_profile(session, membership)
    row = await db.select_session(session, membership.trip_id, session_id)
    if row is None or row.profile_id != owner:
        raise SessionNotFoundError(str(session_id))
    return _history(row)


async def append_messages(
    session: AsyncSession,
    membership: TripMembership,
    session_id: UUID,
    messages: Sequence[ModelMessage],
) -> None:
    """Add the messages of a finished run to the stored history.

    The run must have been started with the host's text as ``user_prompt`` (see
    the module docstring), so ``result.new_messages()`` holds the user turn, the
    tool calls and the answer. Messages that came from a client are not history
    and must never be passed here.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        session_id: The AG-UI ``threadId``.
        messages: ``result.new_messages()`` of a run the server executed.

    Raises:
        SessionNotFoundError: No such session on this trip.
    """
    owner = await owner_profile(session, membership)
    row = await db.select_session(session, membership.trip_id, session_id, lock=True)
    if row is None or row.profile_id != owner:
        raise SessionNotFoundError(str(session_id))
    row.history = [
        *row.history,
        *ModelMessagesTypeAdapter.dump_python(list(messages), mode="json"),
    ]
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
    owner = await owner_profile(session, membership)
    if owner is not None:
        trip, people, preferences = _own_view(trip, people, preferences, owner)
    current = knowledge.values(trip, people, preferences)
    stored = await db.select_assistant_digests(session, membership.trip_id)
    missing = knowledge.missing(current)
    if owner is not None:  # the trip's data is the host's to collect
        missing = [ref for ref in missing if ref.field is KnowledgeField.PREFERENCES]
    return KnowledgeRead(
        trip=trip,
        people=people,
        preferences=preferences,
        missing=missing,
        sources=knowledge.sources(current, stored),
    )


def _own_view(
    trip: TripRead,
    people: list[ProfileRead],
    preferences: list[PreferencesRead],
    owner: UUID,
) -> tuple[TripRead, list[ProfileRead], list[PreferencesRead]]:
    # What a member's panel shows: the trip without its budget, and only themselves.
    return (
        trip.model_copy(update=dict.fromkeys(constants.TRIP_BUDGET_FIELDS)),
        [p for p in people if p.id == owner],
        [p for p in preferences if p.profile_id == owner],
    )


async def mark_assistant_values(
    session: AsyncSession, membership: TripMembership, refs: Sequence[FieldRef]
) -> None:
    """Record that the assistant just wrote these values.

    Call it right after the agent tool saved them through the ``trips`` or
    ``profiles`` services. If the host edits a value afterwards, its source
    turns into ``host`` by itself.

    The digest is taken from a fresh read, not from the value the tool wrote.
    If the host edits the same value between the tool's write and this call,
    that edit is recorded as the assistant's (a small window; call it right
    after the write). If the host re-enters exactly the assistant's value, the
    digest still matches and the source stays ``assistant``.

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
