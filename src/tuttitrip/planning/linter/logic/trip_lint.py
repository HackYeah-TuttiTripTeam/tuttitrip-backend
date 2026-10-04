"""Build a lint request from what the planner knows (pure).

Two entry points: ``context_of`` turns the planning input of a trip into the
context the rules compare a plan with, and ``plan_of`` turns a stored plan into
the linter's own plan structure. Lodging offers and the lunch window are not
part of a generated plan, so those two checks stay off.
"""

import datetime as dt
import unicodedata
from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.linter.schemas import (
    LintContext,
    LintDay,
    LintItem,
    LintPerson,
    LintPlan,
)
from tuttitrip.planning.plans.schemas import PlanRead
from tuttitrip.planning.schemas import PlanningInput, PlanningPerson

MIN_CONTAINED_NAME = 4
"""A catalog name shorter than this is only matched exactly (too many false hits)."""
_TRANSLITERATED = str.maketrans({"ł": "l", "ø": "o", "đ": "d", "ß": "ss", "æ": "ae"})


def _person(person: PlanningPerson, name: str) -> LintPerson:
    return LintPerson(
        id=person.id,
        name=name,
        segment_km=person.segment_km,
        daily_km=person.daily_km,
        nap_start=person.nap_start,
        nap_minutes=person.nap_minutes,
        stairs_sensitivity=person.stairs_sensitivity,
    )


def context_of(planning: PlanningInput, names: Mapping[UUID, str]) -> LintContext:
    """The context of a trip: catalog, people, zone and budget.

    Args:
        planning: The algorithm input of the trip.
        names: Display names by profile id.

    Returns:
        A context without lunch and lodging (those checks are off).
    """
    trip = planning.trip
    return LintContext(
        places=list(planning.places),
        people=[_person(p, str(names.get(p.id, p.id))) for p in planning.people],
        timezone=trip.timezone,
        budget=trip.budget_to,
        flex_pct=trip.flex_pct,
    )


def plan_of(plan: PlanRead, days: tuple[dt.date, ...], people: int) -> LintPlan:
    """A stored plan in the linter's structure.

    Args:
        plan: The stored plan.
        days: Dates of the trip, for days stored without a date.
        people: Group size; the plan stores prices per person.

    Returns:
        The plan: stops with the group cost and the verification flag.
    """
    return LintPlan(
        days=[
            LintDay(
                day=day.date or days[day.index - 1],
                items=[
                    LintItem(
                        name=stop.name,
                        place_id=stop.place_id,
                        start=stop.start,
                        end=stop.end,
                        cost=(stop.price_base or Decimal(0)) * people,
                        price_verified=stop.price_verified,
                    )
                    for stop in day.items
                ],
            )
            for day in plan.days
        ]
    )


def normalize(name: str) -> str:
    """Case, accents and punctuation removed, for comparing names.

    Args:
        name: A place name as typed.

    Returns:
        Lower-case letters and digits only.
    """
    folded = unicodedata.normalize("NFKD", name.casefold().translate(_TRANSLITERATED))
    return "".join(c for c in folded if c.isalnum())


def match_place(name: str, places: list[PlaceRead]) -> PlaceRead | None:
    """The catalog place a typed name means, or None when it is not sure.

    Exact (normalised) equality wins. Otherwise one place whose name is contained
    in the typed text (or the other way round) is taken; two candidates mean no
    match, since a wrong guess hides a real "unknown place" violation.

    Args:
        name: The name as written in the plan.
        places: The city's catalog.

    Returns:
        The place, or None.
    """
    wanted = normalize(name)
    if not wanted:
        return None
    exact = [p for p in places if normalize(p.name) == wanted]
    if exact:
        return exact[0] if len(exact) == 1 else None
    partial = [
        p
        for p in places
        if len(key := normalize(p.name)) >= MIN_CONTAINED_NAME
        and (key in wanted or (len(wanted) >= MIN_CONTAINED_NAME and wanted in key))
    ]
    return partial[0] if len(partial) == 1 else None
