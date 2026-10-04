"""Interview tools: the trip, the people and the budget (issue #57).

Every tool writes through the ``trips`` and ``profiles`` services, takes the
membership from ``deps`` and returns a ``STATE_SNAPSHOT``. The model never
writes to the database and never names the trip.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from tuttitrip.interview import constants
from tuttitrip.interview.schemas import FieldRef, KnowledgeField
from tuttitrip.interview.services.interview_deps import InterviewDeps
from tuttitrip.interview.services.tool_support import (
    host_conflict,
    saved,
    tool_session,
)
from tuttitrip.profiles.schemas import ProfileCreate, ProfileUpdate
from tuttitrip.profiles.services import profile_service
from tuttitrip.trips.schemas import TripUpdate
from tuttitrip.trips.services import trip_service

trip_toolset = FunctionToolset[InterviewDeps]()

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


@trip_toolset.tool
async def set_trip_basics(
    ctx: RunContext[InterviewDeps],
    city: Annotated[str, Field(min_length=1, max_length=200)],
    start_date: date,
    days: Annotated[int, Field(ge=1, le=constants.MAX_TRIP_DAYS)],
    *,
    overwrite_host_values: bool = False,
) -> ToolReturn | str:
    """Save the destination and the dates of the trip.

    Work out ``start_date`` from the calendar in the instructions ("od soboty"
    is the date of that Saturday); the code adds the days.

    Args:
        ctx: The run context.
        city: The destination city.
        start_date: First day of the trip, ISO date.
        days: How many days the trip lasts, counting the first.
        overwrite_host_values: True only after the host agreed to replace a value
            they entered themselves.

    Returns:
        The saved values and a fresh "What we already know" snapshot.
    """
    refs = [
        FieldRef(field=KnowledgeField.DESTINATION),
        FieldRef(field=KnowledgeField.DATES),
    ]
    async with tool_session(ctx) as session:
        if clash := await host_conflict(
            ctx, session, refs, overwrite=overwrite_host_values
        ):
            return clash
        end = start_date + timedelta(days=days - 1)
        trip = await trip_service.update_trip(
            session,
            ctx.deps.membership,
            TripUpdate(destination=city, start_date=start_date, end_date=end),
        )
        done = {
            "destination": trip.destination,
            "start_date": trip.start_date,
            "end_date": trip.end_date,
        }
        return await saved(ctx, session, refs, done)


@trip_toolset.tool
async def add_person(
    ctx: RunContext[InterviewDeps],
    name: Annotated[str, Field(min_length=1, max_length=100)],
    age: Annotated[int, Field(ge=0, le=120)],
) -> ToolReturn:
    """Add a person of the group. Comfort defaults come from the age.

    A name such as "babcia" or "Kasia" is only a label: later tools use the
    returned ``person_id``. Do not add the host again: they are already there.

    Args:
        ctx: The run context.
        name: How the host calls the person.
        age: Age in years; for "babcia" take a plausible age and say so.

    Returns:
        The new ``person_id`` and a fresh snapshot.
    """
    async with tool_session(ctx) as session:
        profile = await profile_service.create_profile(
            session,
            ctx.deps.membership,
            ProfileCreate(display_name=name, age=age),
        )
        ref = FieldRef(field=KnowledgeField.PEOPLE, profile_id=profile.id)
        done = {
            "person_id": str(profile.id),
            "display_name": profile.display_name,
            "age_group": profile.age_group,
        }
        return await saved(ctx, session, [ref], done)


@trip_toolset.tool
async def update_person(
    ctx: RunContext[InterviewDeps],
    person_id: UUID,
    name: Annotated[str | None, Field(min_length=1, max_length=100)] = None,
    age: Annotated[int | None, Field(ge=0, le=120)] = None,
    *,
    overwrite_host_values: bool = False,
) -> ToolReturn | str:
    """Change the name or the age of a person already added.

    Args:
        ctx: The run context.
        person_id: The id returned by ``add_person`` or listed in the instructions.
        name: New name, or omit to keep it.
        age: New age, or omit to keep it.
        overwrite_host_values: True only after the host agreed to replace a value
            they entered themselves.

    Returns:
        The person after the change and a fresh snapshot.
    """
    ref = FieldRef(field=KnowledgeField.PEOPLE, profile_id=person_id)
    async with tool_session(ctx) as session:
        if clash := await host_conflict(
            ctx, session, [ref], overwrite=overwrite_host_values
        ):
            return clash
        profile = await profile_service.update_profile(
            session,
            ctx.deps.membership,
            person_id,
            ProfileUpdate(display_name=name, age=age),
        )
        done = {
            "person_id": str(profile.id),
            "display_name": profile.display_name,
            "age": profile.age,
            "age_group": profile.age_group,
        }
        return await saved(ctx, session, [ref], done)


@trip_toolset.tool
async def set_budget(  # ruff: ignore[too-many-arguments] the tool schema
    ctx: RunContext[InterviewDeps],
    min: Money,  # ruff: ignore[builtin-argument-shadowing] the tool's argument name
    max: Money,  # ruff: ignore[builtin-argument-shadowing] the tool's argument name
    scope: Literal["total", "day"],
    margin: Annotated[int, Field(ge=0, le=50)] = 0,
    *,
    overwrite_host_values: bool = False,
) -> ToolReturn | str:
    """Save the budget range of the trip.

    Args:
        ctx: The run context.
        min: Lower end of the range, in the trip's currency.
        max: Upper end of the range; not below ``min``.
        scope: ``total`` for the whole trip, ``day`` for one day.
        margin: How many percent over the upper end the host tolerates, 0 to 50.
        overwrite_host_values: True only after the host agreed to replace a value
            they entered themselves.

    Returns:
        The saved range and a fresh snapshot.
    """
    ref = FieldRef(field=KnowledgeField.BUDGET)
    async with tool_session(ctx) as session:
        if clash := await host_conflict(
            ctx, session, [ref], overwrite=overwrite_host_values
        ):
            return clash
        current = await trip_service.get_trip(session, ctx.deps.membership)
        total = scope == "total"
        update = TripUpdate(
            currency=current.currency or constants.DEFAULT_CURRENCY,
            budget_total_min=min if total else None,
            budget_total_max=max if total else None,
            budget_day_min=None if total else min,
            budget_day_max=None if total else max,
            budget_flex_pct=margin,
        )
        trip = await trip_service.update_trip(session, ctx.deps.membership, update)
        done = {
            "currency": trip.currency,
            "scope": scope,
            "min": min,
            "max": max,
            "margin": trip.budget_flex_pct,
        }
        return await saved(ctx, session, [ref], done)
