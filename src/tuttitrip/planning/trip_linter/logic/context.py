"""The linter's context from the planning input the solver also reads (pure)."""

from collections.abc import Iterable, Mapping
from uuid import UUID

from tuttitrip.planning.linter.schemas import LintContext, LintPerson
from tuttitrip.planning.schemas import PlanningInput

UNNAMED = "?"


def lint_context(
    data: PlanningInput,
    names: Mapping[UUID, str],
    wheelchair: Iterable[UUID] = (),
) -> LintContext:
    """The same people, catalog, zone and budget the solver planned with.

    No lunch window and no lodging offers: the trip has no such settings, so
    those rules stay off (null disables them).

    Args:
        data: The planning input of the trip.
        names: Display names by profile id.
        wheelchair: Profile ids with the wheelchair limit.

    Returns:
        The context.
    """
    users = frozenset(wheelchair)
    people = [
        LintPerson(
            id=p.id,
            name=names.get(p.id) or UNNAMED,
            segment_km=p.segment_km,
            daily_km=p.daily_km,
            nap_start=p.nap_start,
            nap_minutes=p.nap_minutes,
            stairs_sensitivity=p.stairs_sensitivity,
            wheelchair=p.id in users,
        )
        for p in data.people
    ]
    return LintContext(
        places=list(data.places),
        people=people,
        timezone=data.trip.timezone,
        budget=data.trip.budget_to,
        flex_pct=data.trip.flex_pct,
    )
