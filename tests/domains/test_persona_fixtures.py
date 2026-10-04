"""Rodziny testowe i miasto testowe (tests/fixtures) jako dane algorytmu."""

import math
from decimal import Decimal

import pytest

from tests.fixtures import expected
from tests.fixtures.city import (
    HOMESTAY,
    LODGING_KEYS,
    city,
    key_of,
    lodging_offers,
    place_id,
    places,
)
from tests.fixtures.personas import (
    CHILD_WEIGHT,
    Persona,
    all_groups,
    clones,
    solo_traveller,
    ty,
)
from tests.fixtures.scenarios import (
    Scenario,
    accessible,
    all_scenarios,
    over_budget,
    reference,
    solo,
)
from tuttitrip.accommodation.logic.contract import evaluate
from tuttitrip.accommodation.schemas import Requirement, RequirementStatus
from tuttitrip.places.schemas import (
    CityRead,
    Cuisine,
    PlaceCategory,
    PlaceRead,
    PlaceTag,
)
from tuttitrip.planning.plans.logic.metrics import jain_index
from tuttitrip.profiles.feedback.schemas import RatingValue
from tuttitrip.profiles.logic.weight_presets import validate_weights
from tuttitrip.profiles.preferences.schemas import MinTagDomain, PreferencesWrite
from tuttitrip.profiles.schemas import ProfileCreate, comfort_problem
from tuttitrip.trips.schemas import TripCreate

RHO = 0.7  # E1: weight of an explicit vote
OWN_PLACE = 0.6  # E5: m_ip at which a place is "own"
STAIRS_LIMIT = 0.9  # E0
SEGMENT_FACTOR = 1.5  # E0


def _cosine(interests: dict[PlaceTag, float], tags: list[PlaceTag]) -> float | None:
    if not interests or not tags:
        return None
    dot = sum(interests.get(t, 0.0) for t in tags)
    norm = math.hypot(*interests.values()) * math.sqrt(len(tags))
    return dot / norm


def _match(person: Persona, place: PlaceRead) -> float:
    """``m_ip`` of E1 (0.5 when there is no data at all)."""
    cos = _cosine(person.preferences.interests, place.tags)
    vote = person.rating_value(key_of(place))
    if vote is RatingValue.NEUTRAL:
        return 0.5 if cos is None else cos
    score = 1.0 if vote is RatingValue.WANT else 0.0
    return (1 - RHO) * (cos or 0.0) + RHO * score


def _allowed(person: Persona, place: PlaceRead) -> bool:
    """The E0 filters that depend only on one person and one place."""
    stairs = person.profile.stairs_sensitivity or 0.0
    if person.preferences.constraints.wheelchair and place.stairs > 0:
        return False
    return (
        place.stairs * stairs < STAIRS_LIMIT
        and place.segment_km <= SEGMENT_FACTOR * (person.profile.segment_km or 0.0)
    )


def test_city_is_a_small_valid_catalog() -> None:
    catalog = places()
    assert CityRead.model_validate(city().model_dump()).slug == "miasto-testowe"
    assert 12 <= len(catalog) <= 15
    assert len({p.id for p in catalog.values()}) == len(catalog)
    for key, place in catalog.items():
        assert PlaceRead.model_validate(place.model_dump()) == place
        assert place.id == place_id(key)
    assert set(LODGING_KEYS) <= set(catalog)
    assert {
        k for k, p in catalog.items() if p.category is PlaceCategory.LODGING
    } == set(LODGING_KEYS)


def test_city_covers_the_data_cases_the_spec_needs() -> None:
    catalog = places().values()
    assert any(p.cuisine is Cuisine.INDIAN for p in catalog)
    assert any(price.verified is False for p in catalog for price in p.prices)
    assert any(not p.prices for p in catalog), "a place with no price row"
    assert any(p.hours.verified is False for p in catalog)
    assert any(p.hours.opening_hours is None for p in catalog)
    assert any(p.stairs >= 0.9 for p in catalog)
    assert any(p.queue_min > 0 for p in catalog)
    assert any(
        price.amount == 0 and price.verified for p in catalog for price in p.prices
    )


def test_loader_is_deterministic_and_ordered() -> None:
    assert list(places()) == list(places())
    assert [p.model_dump_json() for p in places().values()] == [
        p.model_dump_json() for p in places().values()
    ]
    first, second = all_scenarios(), all_scenarios()
    assert (
        list(first)
        == list(second)
        == ["reference", "solo", "over_budget", "friends", "accessible"]
    )
    for key in first:
        assert first[key] == second[key]
        assert [p.id for p in first[key].group.people] == [
            p.id for p in second[key].group.people
        ]
    assert list(all_groups()) == ["reference", "solo", "friends", "accessible"]


def test_loader_does_not_share_mutable_state() -> None:
    places()["zoo"].tags.clear()
    assert places()["zoo"].tags


@pytest.mark.parametrize("scenario", all_scenarios().values(), ids=lambda s: s.key)
def test_every_scenario_validates_against_the_schemas(scenario: Scenario) -> None:
    TripCreate.model_validate(scenario.trip.model_dump())
    validate_weights([p.weight for p in scenario.group.people])
    scenario.group.weights_update()
    assert len({p.id for p in scenario.group.people}) == len(scenario.group.people)
    for person in scenario.group.people:
        ProfileCreate.model_validate(person.profile.model_dump())
        PreferencesWrite.model_validate(person.preferences.model_dump())
        profile = person.profile
        assert profile.segment_km
        assert profile.daily_km
        assert not comfort_problem(
            profile.segment_km,
            profile.daily_km,
            profile.nap_start,
            profile.nap_minutes or 0,
        )
        assert sum(person.pool.model_dump().values()) == 10
        for key in (*person.ratings, *person.vetoes):
            assert key in places()


@pytest.mark.parametrize("scenario", all_scenarios().values(), ids=lambda s: s.key)
def test_min_tags_point_at_real_places(scenario: Scenario) -> None:
    catalog = places().values()
    for person in scenario.group.people:
        for tag in person.preferences.min_tags:
            if tag.domain is MinTagDomain.FOOD:
                assert any(p.cuisine and p.cuisine.value == tag.tag for p in catalog)
            else:
                assert any(tag.tag in {t.value for t in p.tags} for p in catalog)


@pytest.mark.parametrize("scenario", all_scenarios().values(), ids=lambda s: s.key)
def test_every_person_has_an_own_place_for_each_day(scenario: Scenario) -> None:
    # Otherwise the "days without an own place" penalty of E5 always fires.
    vetoed = {k for p in scenario.group.people for k in p.vetoes}
    visitable = [
        (key, place)
        for key, place in places().items()
        if place.category is not PlaceCategory.LODGING and key not in vetoed
    ]
    for person in scenario.group.people:
        own = [
            key
            for key, place in visitable
            if _allowed(person, place) and _match(person, place) >= OWN_PLACE
        ]
        assert len(own) >= scenario.days, (person.key, own)


def test_reference_group_matches_the_issue() -> None:
    scenario = reference()
    people = {p.key: p for p in scenario.group.people}
    assert list(people) == ["ty", "kasia", "tomek", "babcia"]
    assert (people["kasia"].profile.age, people["tomek"].profile.age) == (6, 13)
    assert scenario.days == 3
    assert (scenario.trip.budget_total_min, scenario.trip.budget_total_max) == (
        1300,
        1700,
    )
    assert people["babcia"].vetoes == ("restauracja_morska",)
    assert places()["restauracja_morska"].cuisine is not None
    assert [(t.domain.value, t.tag) for t in people["tomek"].preferences.min_tags] == [
        ("food", "indian")
    ]
    assert [p.weight for p in people.values()] == [1.0, CHILD_WEIGHT, CHILD_WEIGHT, 1.0]
    assert {p.profile.floor for p in people.values()} == {30, 35}
    assert people["babcia"].pool.food == 2


def test_lodging_offers_agree_with_place_amenities() -> None:
    for key, offer in lodging_offers().items():
        assert offer.present == {a.value for a in places()[key].amenities}
        assert not offer.present & offer.absent


def test_reference_lodging_has_exactly_one_pool_offer() -> None:
    offers = lodging_offers()
    assert set(offers) == set(LODGING_KEYS)
    pool = Requirement(feature="pool")
    met = [
        k
        for k, o in offers.items()
        if evaluate(pool, o).status is RequirementStatus.MET
    ]
    assert met == [HOMESTAY]
    assert "pool" in {a.value for a in places()[HOMESTAY].amenities}
    # The soft elevator is unconfirmed there: the three-state contract in one fixture.
    elevator = evaluate(Requirement(feature="elevator"), offers[HOMESTAY])
    assert elevator.status is RequirementStatus.UNCONFIRMED
    assert {r.key for r in reference().requirements.requirements if r.hard} == {"pool"}


def test_accessible_scenario_has_one_lodging_for_its_hard_requirements() -> None:
    hard = [
        Requirement(feature=r.key)
        for r in accessible().requirements.requirements
        if r.hard
    ]
    ok = [
        key
        for key, offer in lodging_offers().items()
        if all(evaluate(r, offer).status is RequirementStatus.MET for r in hard)
    ]
    assert ok == ["hotel_centrum"]


def test_solo_and_over_budget_variants() -> None:
    assert (
        solo().days,
        solo().trip.budget_total_min,
        solo().trip.budget_total_max,
    ) == (
        2,
        500,
        800,
    )
    assert [p.key for p in solo().group.people] == ["ty"]
    variant = over_budget()
    assert variant.group == reference().group
    assert (variant.trip.budget_total_min, variant.trip.budget_total_max) == (900, 1100)
    assert variant.b_max == Decimal(1210)
    # The spec's 1198 zl fits B_max but exceeds B_do: exactly the approval case.
    assert variant.b_do < expected.OVER_BUDGET_COST <= variant.b_max
    assert expected.OVER_BUDGET_COST - variant.b_do == expected.OVER_BUDGET_EXCESS


def test_clones_are_solo_copies_with_own_ids() -> None:
    group = clones(ty(), 3)
    assert len({p.id for p in group.people}) == 3
    for clone in group.people:
        assert (clone.profile, clone.weight, clone.preferences, clone.ratings) == (
            ty().profile,
            ty().weight,
            ty().preferences,
            ty().ratings,
        )
    assert solo_traveller().people[0] == ty()


def test_friends_and_accessible_groups_differ_on_purpose() -> None:
    scenarios = all_scenarios()
    assert len(scenarios["friends"].group.people) == 3
    assert len(scenarios["accessible"].group.people) == 3
    constraints = [
        p.preferences.constraints for p in scenarios["accessible"].group.people
    ]
    assert sum(c.wheelchair for c in constraints) == 1
    assert any(
        "vegan" in {t.value for t in p.preferences.diet.tags}
        for p in scenarios["friends"].group.people
    )


# --- tabela z sekcji 7: spójność wewnętrzna, bez solvera ---


def test_expected_table_is_consistent_with_the_floor_and_r_formulas() -> None:
    people = {p.key: p for p in reference().group.people}
    for key, row in expected.REFERENCE_PEOPLE.items():
        assert row.floor == pytest.approx(
            min(people[key].profile.floor or 0, 0.6 * row.u_star), abs=0.05
        ), key
        r = min(1.0, (row.u + 10) / (row.u_star + 10))
        assert round(r * 100) == row.r_percent, key
    rs = [
        min(1.0, (row.u + 10) / (row.u_star + 10))
        for row in expected.REFERENCE_PEOPLE.values()
    ]
    assert jain_index(rs) == pytest.approx(expected.REFERENCE_JAIN, abs=0.001)
    assert min(rs) == pytest.approx(expected.REFERENCE_MIN_R, abs=0.005)
    assert set(expected.REFERENCE_PLAN[0]) <= set(places())
    assert reference().b_do >= expected.REFERENCE_COST
    assert expected.REFERENCE_LODGING == HOMESTAY


@pytest.mark.skip(reason=expected.SKIP_REASON)
def test_reference_plan_matches_the_spec_table() -> None:
    # Active once demo_data.py and the solver are in (u*, u, r, floor, cost, hash).
    raise NotImplementedError
