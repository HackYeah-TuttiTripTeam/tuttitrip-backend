"""Shared parts of the interview tools: snapshots, the host's-values guard, errors."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import ModelRetry, RunContext, ToolReturn
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
    "NOT SAVED: the host set {what} themselves. Ask the host whether to overwrite "
    "it, then call this tool again with overwrite_host_values=true."
)
UNKNOWN_PERSON = "There is no such person on this trip; use a person_id from the tools."


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


async def host_conflict(
    ctx: Ctx, session: AsyncSession, refs: list[FieldRef]
) -> str | None:
    """Tell whether the host already set one of the values.

    Args:
        ctx: The run context.
        session: Open session.
        refs: The values the tool is about to write.

    Returns:
        The message to return to the model, or None when the write is free.
    """
    current = await session_service.get_knowledge(session, ctx.deps.membership)
    clash = knowledge.host_set(current.sources, refs)
    if not clash:
        return None
    return NOT_SAVED.format(what=", ".join(sorted({r.field.value for r in clash})))


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
