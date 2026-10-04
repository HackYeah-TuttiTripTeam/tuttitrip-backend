"""Shared parts of the interview tools: snapshots, the host's-values guard, errors."""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import ModelRetry, RunContext, ToolReturn
from pydantic_ai.messages import ModelMessage, ToolReturnPart, UserPromptPart
from pydantic_ai.tools import ToolDefinition
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.interview.logic import knowledge
from tuttitrip.interview.schemas import FieldRef
from tuttitrip.interview.services import session_service
from tuttitrip.interview.services.interview_deps import InterviewDeps
from tuttitrip.profiles.services.profile_service import (
    ProfileComfortError,
    ProfileNotFoundError,
)
from tuttitrip.trips.services.trip_service import TripInvalidError

type Ctx = RunContext[InterviewDeps]

NOT_SAVED = (
    "NOT SAVED: the host set {what} themselves. Ask the host in your reply whether "
    "to overwrite it. Only when they agree in a later message, call this tool "
    "again with overwrite_host_values=true. Refs: {refs}"
)
UNKNOWN_PERSON = "There is no such person on this trip; use a person_id from the tools."


def for_host(ctx: Ctx, _tool: ToolDefinition) -> bool:
    """Tool filter: the tool belongs to the trip's interview (host and co-hosts).

    Args:
        ctx: The run context.
        _tool: The tool being offered.

    Returns:
        False in a member's interview.
    """
    return not ctx.deps.is_member


def for_member(ctx: Ctx, _tool: ToolDefinition) -> bool:
    """Tool filter: the tool belongs to a member's own interview.

    Args:
        ctx: The run context.
        _tool: The tool being offered.

    Returns:
        True only in a member's interview.
    """
    return ctx.deps.is_member


@asynccontextmanager
async def tool_session(ctx: Ctx) -> AsyncGenerator[AsyncSession]:
    """Open a session for one tool call and turn domain errors into retries.

    Args:
        ctx: The run context.

    Yields:
        An open session.

    Raises:
        ModelRetry: The domain refused the write; the message goes to the model.
    """
    async with ctx.deps.sessions() as session:
        try:
            yield session
        except ProfileNotFoundError as exc:
            raise ModelRetry(UNKNOWN_PERSON) from exc
        except (ProfileComfortError, TripInvalidError) as exc:
            await session.rollback()
            message = f"Rejected, nothing was saved: {exc}"
            raise ModelRetry(message) from exc


def _ref_key(ref: FieldRef) -> str:
    return f"[{ref.field.value}:{ref.profile_id or '-'}]"


def overwrite_confirmed(
    messages: Sequence[ModelMessage], tool: str, keys: set[str]
) -> bool:
    """Whether the host was asked: this tool refused these values, then they spoke.

    Args:
        messages: The run's messages including the stored history.
        tool: The tool asking to overwrite.
        keys: The refs it wants to overwrite.

    Returns:
        True when the latest refusal of this tool for all the refs is followed
        by a new user message.
    """
    refused_at = -1
    for index, message in enumerate(messages):
        for part in message.parts:
            if (
                isinstance(part, ToolReturnPart)
                and part.tool_name == tool
                and isinstance(part.content, str)
                and part.content.startswith("NOT SAVED")
                and all(key in part.content for key in keys)
            ):
                refused_at = index
    return refused_at >= 0 and any(
        isinstance(part, UserPromptPart)
        for message in messages[refused_at + 1 :]
        for part in message.parts
    )


async def host_conflict(
    ctx: RunContext[InterviewDeps],
    session: AsyncSession,
    refs: list[FieldRef],
    *,
    overwrite: bool,
) -> str | None:
    """Tell whether the host already set one of the values and may not be overruled.

    Args:
        ctx: The run context.
        session: Open session.
        refs: The values the tool is about to write.
        overwrite: The model asked to overwrite; honoured only after the host
            was asked (see ``overwrite_confirmed``).

    Returns:
        The message to return to the model, or None when the write is free.
    """
    if ctx.deps.is_member:  # a member's own data: there is no host value to protect
        return None
    current = await session_service.get_knowledge(session, ctx.deps.membership)
    clash = knowledge.host_set(current.sources, refs)
    if not clash:
        return None
    keys = {_ref_key(ref) for ref in clash}
    if overwrite and overwrite_confirmed(ctx.messages, ctx.tool_name or "", keys):
        return None
    what = ", ".join(sorted({r.field.value for r in clash}))
    return NOT_SAVED.format(what=what, refs=" ".join(sorted(keys)))


async def saved(
    ctx: Ctx, session: AsyncSession, refs: list[FieldRef], value: object
) -> ToolReturn:
    """Mark the written values as the assistant's and push a fresh snapshot.

    Args:
        ctx: The run context.
        session: Open session.
        refs: The values the tool just wrote.
        value: What the model sees as the tool result.

    Returns:
        The result with a ``STATE_SNAPSHOT`` of the "What we already know" panel.
    """
    if not ctx.deps.is_member:
        # A member's values stay unmarked on purpose: they read as "set by a
        # person", so the host's assistant asks before it changes them.
        await session_service.mark_assistant_values(session, ctx.deps.membership, refs)
    return await snapshot(ctx, session, value)


async def snapshot(ctx: Ctx, session: AsyncSession, value: object) -> ToolReturn:
    """Read the panel and wrap it in a ``STATE_SNAPSHOT`` event.

    Args:
        ctx: The run context.
        session: Open session.
        value: What the model sees as the tool result.

    Returns:
        The result; the event travels in ``metadata``.
    """
    state = ctx.deps.state
    state.knowledge = await session_service.get_knowledge(session, ctx.deps.membership)
    return ToolReturn(
        return_value=value,
        metadata=[
            StateSnapshotEvent(
                type=EventType.STATE_SNAPSHOT, snapshot=state.model_dump(mode="json")
            )
        ],
    )
