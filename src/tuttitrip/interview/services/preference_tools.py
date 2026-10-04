"""Interview tools: constraints, diet, interests and the importance pool (issue #58).

They edit one person's preferences through ``preference_service``. Argument
types are the closed lists of the preferences domain, so a value outside them
fails validation and goes back to the model without touching the database.
"""

from typing import Annotated
from uuid import UUID

from pydantic import Field
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.interview import constants
from tuttitrip.interview.logic import importance
from tuttitrip.interview.schemas import ConstraintKind, FieldRef, KnowledgeField
from tuttitrip.interview.services import decision_service
from tuttitrip.interview.services.interview_deps import InterviewDeps
from tuttitrip.interview.services.tool_support import (
    host_conflict,
    saved,
    tool_session,
)
from tuttitrip.places.schemas import DietTag, PlaceTag
from tuttitrip.profiles.preferences.schemas import (
    Constraints,
    Diet,
    ImportanceDomain,
    ImportancePool,
    PoolPoints,
    PreferencesRead,
    PreferencesWrite,
)
from tuttitrip.profiles.preferences.services import preference_service

preference_toolset = FunctionToolset[InterviewDeps]()

NEEDS_CONFIRMATION = (
    "NOT SAVED: I am not sure enough ({confidence:.0%}) that this means '{value}'. "
    "Ask the host to confirm with a confirm card, then call the explicit tool "
    "({tool}) with the confirmed value."
)


def _writable(read: PreferencesRead) -> PreferencesWrite:
    return PreferencesWrite(
        interests=read.interests,
        diet=read.diet,
        example_places=read.example_places,
        min_tags=read.min_tags,
        constraints=read.constraints or Constraints(),
        importance_pool=read.importance_pool,
    )


async def _current(
    ctx: RunContext[InterviewDeps], session: AsyncSession, person_id: UUID
) -> PreferencesWrite:
    read = await preference_service.get_preferences(
        session, ctx.deps.membership, person_id
    )
    return _writable(read)


async def _save(
    ctx: RunContext[InterviewDeps],
    session: AsyncSession,
    person_id: UUID,
    write: PreferencesWrite,
    done: dict[str, object],
) -> ToolReturn:
    await preference_service.replace_preferences(
        session, ctx.deps.membership, person_id, write
    )
    ref = FieldRef(field=KnowledgeField.PREFERENCES, profile_id=person_id)
    return await saved(ctx, session, [ref], {"person_id": str(person_id), **done})


async def _guard(
    ctx: RunContext[InterviewDeps],
    session: AsyncSession,
    person_id: UUID,
    *,
    overwrite: bool,
) -> str | None:
    ref = FieldRef(field=KnowledgeField.PREFERENCES, profile_id=person_id)
    return await host_conflict(ctx, session, [ref], overwrite=overwrite)


async def _set_constraint(
    ctx: RunContext[InterviewDeps],
    person_id: UUID,
    kind: ConstraintKind,
    *,
    value: bool,
    overwrite: bool,
) -> ToolReturn | str:
    async with tool_session(ctx) as session:
        if clash := await _guard(ctx, session, person_id, overwrite=overwrite):
            return clash
        write = await _current(ctx, session, person_id)
        write.constraints = write.constraints.model_copy(update={kind.value: value})
        return await _save(ctx, session, person_id, write, {kind.value: value})


async def _set_diet(
    ctx: RunContext[InterviewDeps],
    person_id: UUID,
    diet: DietTag,
    *,
    enabled: bool,
    overwrite: bool,
) -> ToolReturn | str:
    async with tool_session(ctx) as session:
        if clash := await _guard(ctx, session, person_id, overwrite=overwrite):
            return clash
        write = await _current(ctx, session, person_id)
        tags = {t for t in write.diet.tags if t != diet}
        if enabled:
            tags.add(diet)
        write.diet = Diet(
            tags=sorted(tags, key=list(DietTag).index),
            allergies=write.diet.allergies,
        )
        return await _save(
            ctx, session, person_id, write, {"diet": diet.value, "enabled": enabled}
        )


@preference_toolset.tool
async def set_constraint(
    ctx: RunContext[InterviewDeps],
    person_id: UUID,
    kind: ConstraintKind,
    *,
    value: bool = True,
    overwrite_host_values: bool = False,
) -> ToolReturn | str:
    """Save an access limit of a person ("babcia nie chodzi po schodach" is stairs).

    Args:
        ctx: The run context.
        person_id: The person.
        kind: Which limit.
        value: True to set it, False to clear it.
        overwrite_host_values: True only after the host agreed to replace values
            they entered themselves.

    Returns:
        The saved limit and a fresh snapshot.
    """
    return await _set_constraint(
        ctx, person_id, kind, value=value, overwrite=overwrite_host_values
    )


@preference_toolset.tool
async def set_diet(
    ctx: RunContext[InterviewDeps],
    person_id: UUID,
    diet: DietTag,
    *,
    enabled: bool = True,
    overwrite_host_values: bool = False,
) -> ToolReturn | str:
    """Save a diet of a person ("Tomek je tylko wegetariańsko" is vegetarian).

    Args:
        ctx: The run context.
        person_id: The person.
        diet: Which diet.
        enabled: True to add it, False to remove it.
        overwrite_host_values: True only after the host agreed to replace values
            they entered themselves.

    Returns:
        The saved diet and a fresh snapshot.
    """
    return await _set_diet(
        ctx, person_id, diet, enabled=enabled, overwrite=overwrite_host_values
    )


@preference_toolset.tool
async def classify_diet(
    ctx: RunContext[InterviewDeps],
    person_id: UUID,
    text: Annotated[str, Field(min_length=1, max_length=constants.MAX_FREE_TEXT)],
) -> ToolReturn | str:
    """Let a decision model read a free sentence about food as a diet and save it.

    It saves only when the model is sure; otherwise it tells you to ask.

    Args:
        ctx: The run context.
        person_id: The person.
        text: The host's words, e.g. "nie jemy mięsa".

    Returns:
        The saved diet and a snapshot, or a request to confirm.
    """
    pick = await decision_service.classify_diet(text)
    if not pick.sure:
        return NEEDS_CONFIRMATION.format(
            confidence=pick.confidence or 0, value=pick.value.value, tool="set_diet"
        )
    return await _set_diet(ctx, person_id, pick.value, enabled=True, overwrite=False)


@preference_toolset.tool
async def classify_constraint(
    ctx: RunContext[InterviewDeps],
    person_id: UUID,
    text: Annotated[str, Field(min_length=1, max_length=constants.MAX_FREE_TEXT)],
) -> ToolReturn | str:
    """Let a decision model read a free sentence as an access limit and save it.

    It saves only when the model is sure; otherwise it tells you to ask.

    Args:
        ctx: The run context.
        person_id: The person.
        text: The host's words, e.g. "babcia nie wchodzi po schodach".

    Returns:
        The saved limit and a snapshot, or a request to confirm.
    """
    pick = await decision_service.classify_constraint(text)
    if not pick.sure:
        return NEEDS_CONFIRMATION.format(
            confidence=pick.confidence or 0,
            value=pick.value.value,
            tool="set_constraint",
        )
    return await _set_constraint(
        ctx, person_id, pick.value, value=True, overwrite=False
    )


@preference_toolset.tool
async def add_interest(
    ctx: RunContext[InterviewDeps],
    person_id: UUID,
    interest: PlaceTag,
    strength: Annotated[float, Field(ge=0, le=1)] = 1.0,
    *,
    overwrite_host_values: bool = False,
) -> ToolReturn | str:
    """Save an interest of a person.

    Args:
        ctx: The run context.
        person_id: The person.
        interest: Which interest.
        strength: How much, from 0 to 1.
        overwrite_host_values: True only after the host agreed to replace values
            they entered themselves.

    Returns:
        The saved interest and a fresh snapshot.
    """
    async with tool_session(ctx) as session:
        if clash := await _guard(
            ctx, session, person_id, overwrite=overwrite_host_values
        ):
            return clash
        write = await _current(ctx, session, person_id)
        write.interests = {**write.interests, interest: strength}
        return await _save(ctx, session, person_id, write, {"interest": interest.value})


@preference_toolset.tool
async def set_importance_points(
    ctx: RunContext[InterviewDeps],
    person_id: UUID,
    domain: ImportanceDomain,
    points: PoolPoints,
    *,
    overwrite_host_values: bool = False,
) -> ToolReturn | str:
    """Give a domain points from the person's pool of ten.

    The pool always adds up to ten, so the other domains give or take points in
    proportion. More than ten points is rejected. For a child the parent sets
    the pool.

    Args:
        ctx: The run context.
        person_id: The person.
        domain: Lodging, food, attractions, pace or cost.
        points: Points for this domain, 0 to ten.
        overwrite_host_values: True only after the host agreed to replace values
            they entered themselves.

    Returns:
        The whole pool after the change and a fresh snapshot.
    """
    async with tool_session(ctx) as session:
        if clash := await _guard(
            ctx, session, person_id, overwrite=overwrite_host_values
        ):
            return clash
        read = await preference_service.get_preferences(
            session, ctx.deps.membership, person_id
        )
        write = _writable(read)
        moved = importance.set_points(
            read.importance_pool.model_dump(), domain.value, points
        )
        write.importance_pool = ImportancePool(**moved)
        return await _save(ctx, session, person_id, write, {"importance_pool": moved})
