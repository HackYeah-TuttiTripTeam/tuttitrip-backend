"""Scenariusze fixtures jako ``PlanningInput`` (wejście algorytmu planowania)."""

from tests.fixtures.city import key_of, places
from tests.fixtures.personas import Persona
from tests.fixtures.scenarios import Scenario
from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.schemas import PlanningInput, PlanningPerson, PlanningTrip, Vote
from tuttitrip.profiles.feedback.schemas import RatingValue
from tuttitrip.profiles.preferences.logic.access import effective_stairs_sensitivity

_VOTES: dict[RatingValue, Vote] = {
    RatingValue.WANT: 1,
    RatingValue.NEUTRAL: 0,
    RatingValue.DONT_WANT: -1,
}


def planning_person(persona: Persona, catalog: dict[str, PlaceRead]) -> PlanningPerson:
    """``Persona`` jako osoba algorytmu (klucze miejsc zamienione na id)."""
    profile = persona.profile
    prefs = persona.preferences
    return PlanningPerson(
        id=persona.id,
        age=profile.age,
        weight=persona.weight,
        interests=prefs.interests,
        pool=persona.pool,
        segment_km=profile.segment_km or 1.0,
        daily_km=profile.daily_km or 1.0,
        active_min=profile.active_min or 60,
        stairs_sensitivity=effective_stairs_sensitivity(
            prefs.constraints, profile.stairs_sensitivity or 0.0
        ),
        queue_patience_min=profile.queue_patience_min or 0,
        floor=profile.floor or 0,
        votes={
            catalog[k].id: _VOTES[r.value]
            for k, r in persona.ratings.items()
            if k in catalog
        },
        vetoes=frozenset(catalog[k].id for k in persona.vetoes),
        min_tags=tuple(prefs.min_tags),
    )


def planning_input(scenario: Scenario, *, lodging: bool = True) -> PlanningInput:
    """Scenariusz jako ``PlanningInput`` ze wszystkimi miejscami miasta."""
    catalog = places()
    trip = scenario.trip
    start, end = trip.start_date, trip.end_date
    assert start is not None
    assert end is not None
    assert trip.day_start is not None
    assert trip.day_end is not None
    assert trip.budget_total_min is not None
    assert trip.budget_total_max is not None
    days = tuple(
        start.fromordinal(o) for o in range(start.toordinal(), end.toordinal() + 1)
    )
    return PlanningInput(
        trip=PlanningTrip(
            days=days,
            timezone="Europe/Warsaw",
            day_start=trip.day_start,
            day_end=trip.day_end,
            budget_from=trip.budget_total_min,
            budget_to=trip.budget_total_max,
            flex_pct=trip.budget_flex_pct or 0,
            has_lodging=lodging,
            currency=trip.currency or "PLN",
        ),
        people=tuple(planning_person(p, catalog) for p in scenario.group.people),
        places=tuple(p for p in catalog.values() if key_of(p)),
    )
