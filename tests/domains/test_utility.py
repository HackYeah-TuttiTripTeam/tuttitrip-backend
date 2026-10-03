"""E1 (utility of a place without cost) and E0 (hard constraints): pure unit tests."""

import math

import pytest

from tests.fixtures.city import key_of, place_id, places
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import all_scenarios, reference
from tuttitrip.places.schemas import PlacePriceRead, PlaceRead, PlaceTag
from tuttitrip.planning.logic.hard_constraints import RejectionCode, filter_places
from tuttitrip.planning.logic.params import DEFAULT_PARAMS
from tuttitrip.planning.logic.utility import (
    effort,
    explain,
    exponents,
    interest_cosine,
    match,
    utility,
)
from tuttitrip.planning.schemas import PlanningInput, PlanningPerson
from tuttitrip.profiles.preferences.schemas import ImportancePool

CATALOG = places()


def person(data: PlanningInput, index: int = 0) -> PlanningPerson:
    return data.people[index]


def with_(p: PlanningPerson, **changes: object) -> PlanningPerson:
    return p.model_copy(update=changes)


@pytest.fixture
def data() -> PlanningInput:
    return planning_input(reference())


@pytest.fixture
def museum() -> PlaceRead:
    return CATALOG["muzeum_miejskie"]


# --- E1: match ---------------------------------------------------------------


def test_params_match_section_6() -> None:
    p = DEFAULT_PARAMS
    assert (p.alpha, p.kappa_attractions, p.kappa_food, p.tau_ref_min) == (
        1,
        0.6,
        1.2,
        90,
    )
    assert (p.epsilon, p.lambda_floor, p.vote_weight, p.unverified_markup) == (
        0.01,
        0.1,
        0.7,
        0.15,
    )
    assert (p.uncertain_requirement, p.strong_preference, p.smoothing) == (0.4, 0.4, 10)
    assert (p.floor_share, p.violation_penalty, p.cost_comfort) == (0.6, 1000, 60)
    assert (p.good_reason_points, p.good_reason_min_r, p.good_reason_welfare) == (
        8,
        0.05,
        0.03,
    )


def person_of() -> PlanningPerson:
    return planning_input(reference()).people[0]


def test_match_acceptance_example() -> None:
    base = CATALOG["muzeum_miejskie"]
    # I = (1 on art-like unrelated tag, 0.2/... ) pick a profile with cos exactly 0.2:
    # place tag set {history}; I = {history: 1, science: sqrt(24)} -> cos = 1/5 = 0.2.
    place = base.model_copy(update={"tags": [PlaceTag.HISTORY]})
    p = with_(
        person_of(),
        interests={
            PlaceTag.HISTORY: 1.0 / math.sqrt(25),
            PlaceTag.SCIENCE: math.sqrt(24) / 5,
        },
        votes={place.id: 1},
    )
    assert interest_cosine(p, place) == pytest.approx(0.2)
    assert match(p, place) == pytest.approx(0.3 * 0.2 + 0.7 * 1)


def test_match_without_profile_and_vote_is_half(museum: PlaceRead) -> None:
    p = with_(person_of(), interests={}, votes={})
    assert match(p, museum) == pytest.approx(0.5)


def test_match_without_vote_is_the_cosine(museum: PlaceRead) -> None:
    p = with_(person_of(), votes={})
    cos = interest_cosine(p, museum)
    assert cos is not None
    assert match(p, museum) == pytest.approx(cos)


def test_match_vote_without_profile_uses_neutral_profile(museum: PlaceRead) -> None:
    p = with_(person_of(), interests={}, votes={museum.id: -1})
    assert match(p, museum) == pytest.approx(0.3 * 0.5)
    neutral = with_(p, votes={museum.id: 0})
    assert match(neutral, museum) == pytest.approx(0.3 * 0.5 + 0.7 * 0.5)


def test_match_place_without_tags_is_no_data(museum: PlaceRead) -> None:
    bare = museum.model_copy(update={"tags": []})
    assert match(with_(person_of(), votes={}), bare) == pytest.approx(0.5)


# --- E1: effort, lambda, utility ----------------------------------------------


def test_effort_formula() -> None:
    p = with_(
        person_of(), segment_km=2.0, stairs_sensitivity=0.5, queue_patience_min=20
    )
    place = CATALOG["muzeum_miejskie"].model_copy(
        update={"segment_km": 1.0, "stairs": 0.4, "queue_min": 10}
    )
    assert effort(p, place) == pytest.approx(0.6 * 0.5 + 0.2 * 0.2 + 0.2 * 0.5)


def test_effort_caps_each_part_and_handles_zero_patience() -> None:
    p = with_(person_of(), segment_km=1.0, queue_patience_min=0, stairs_sensitivity=0.0)
    far = CATALOG["muzeum_miejskie"].model_copy(
        update={"segment_km": 3.0, "queue_min": 5}
    )
    assert effort(p, far) == pytest.approx(0.6 + 0.2)
    calm = far.model_copy(update={"queue_min": 0})
    assert effort(p, calm) == pytest.approx(0.6)


def test_exponents_sum_to_one_and_follow_the_pool(museum: PlaceRead) -> None:
    p = with_(
        person_of(),
        pool=ImportancePool(lodging=0, food=0, attractions=6, pace=4, cost=0),
    )
    lam_m, lam_e = exponents(p, museum, has_lodging=True)
    assert lam_m + lam_e == pytest.approx(1)
    assert lam_m == pytest.approx((0.6 + 0.1) / (0.6 + 0.4 + 0.2))
    assert lam_e == pytest.approx((0.4 + 0.1) / (0.6 + 0.4 + 0.2))


def test_exponent_of_a_zero_domain_is_a_tenth_over_z(museum: PlaceRead) -> None:
    p = with_(
        person_of(),
        pool=ImportancePool(lodging=0, food=0, attractions=0, pace=10, cost=0),
    )
    lam_m, lam_e = exponents(p, museum, has_lodging=True)
    z = 0.1 + 1.1
    assert lam_m == pytest.approx(0.1 / z)
    assert lam_e == pytest.approx(1.1 / z)
    assert 0 <= utility(p, museum, has_lodging=True) <= 100


def test_pool_is_renormalised_to_active_domains(museum: PlaceRead) -> None:
    p = with_(
        person_of(),
        pool=ImportancePool(lodging=5, food=0, attractions=3, pace=2, cost=0),
    )
    with_lodging = exponents(p, museum, has_lodging=True)
    without = exponents(p, museum, has_lodging=False)
    assert without[0] == pytest.approx((0.6 + 0.1) / (0.6 + 0.4 + 0.2))
    assert with_lodging[0] == pytest.approx((0.3 + 0.1) / (0.3 + 0.2 + 0.2))


def test_food_places_use_the_food_domain() -> None:
    p = with_(
        person_of(),
        pool=ImportancePool(lodging=0, food=8, attractions=0, pace=2, cost=0),
    )
    food = CATALOG["bar_mleczny"]
    sight = CATALOG["muzeum_miejskie"]
    assert (
        exponents(p, food, has_lodging=False)[0]
        > exponents(p, sight, has_lodging=False)[0]
    )


def test_utility_formula_and_cap() -> None:
    p = person_of()
    place = CATALOG["muzeum_miejskie"]
    m, e = match(p, place), effort(p, place)
    lam_m, lam_e = exponents(p, place, has_lodging=True)
    expected = 100 * (m + 0.01) ** lam_m * (1 - e + 0.01) ** lam_e
    assert utility(p, place, has_lodging=True) == pytest.approx(min(100, expected))
    perfect = with_(p, votes={place.id: 1}, interests={place.tags[0]: 1.0})
    free = place.model_copy(update={"segment_km": 0.0, "stairs": 0.0, "queue_min": 0})
    assert utility(perfect, free, has_lodging=True) <= 100


def test_price_does_not_change_utility(museum: PlaceRead) -> None:
    p = person_of()
    cheap = museum.model_copy(update={"prices": [_price(museum, "1")]})
    dear = museum.model_copy(update={"prices": [_price(museum, "999")]})
    assert utility(p, cheap, has_lodging=True) == utility(p, dear, has_lodging=True)
    assert utility(p, cheap, has_lodging=True) == utility(p, museum, has_lodging=True)


def _price(place: PlaceRead, amount: str) -> PlacePriceRead:
    return place.prices[0].model_copy(update={"amount": amount})


def test_explain_matches_the_parts(museum: PlaceRead) -> None:
    p = person_of()
    card = explain(p, museum, has_lodging=True)
    assert card.person_id == p.id
    assert card.place_id == museum.id
    assert card.match == match(p, museum)
    assert card.effort == effort(p, museum)
    assert card.utility == utility(p, museum, has_lodging=True)


def test_utility_stays_in_range_for_every_person_and_place() -> None:
    for scenario in all_scenarios().values():
        data = planning_input(scenario)
        for p in data.people:
            for place in data.places:
                for lodging in (True, False):
                    card = explain(p, place, has_lodging=lodging)
                    assert 0 <= card.utility <= 100
                    assert 0 <= card.match <= 1
                    assert 0 <= card.effort <= 1


def test_wanted_place_beats_unwanted_one_for_the_same_person() -> None:
    data = planning_input(reference())
    babcia = data.people[3]
    park = CATALOG["park_oliwski"]
    assert babcia.votes[park.id] == 1
    liked = utility(babcia, park, has_lodging=True)
    disliked = utility(with_(babcia, votes={park.id: -1}), park, has_lodging=True)
    assert liked > disliked


# --- E0: hard constraints -------------------------------------------------------


def reasons(result, key: str) -> set[RejectionCode]:  # ruff: ignore[missing-type-function-argument]
    return {r.code for r in result.reasons(place_id(key))}


def test_veto_rejects_the_place(data: PlanningInput) -> None:
    result = filter_places(data)
    assert RejectionCode.VETO in reasons(result, "restauracja_morska")
    assert place_id("restauracja_morska") not in {p.id for p in result.accepted}


def test_veto_names_the_person(data: PlanningInput) -> None:
    result = filter_places(data)
    rejection = next(
        r
        for r in result.reasons(place_id("restauracja_morska"))
        if r.code is RejectionCode.VETO
    )
    assert rejection.person_id == data.people[3].id


def test_stairs_reject_at_the_limit(museum: PlaceRead) -> None:
    data = planning_input(reference())
    tower = replace_place(data, museum, stairs=1.0)
    sensitive = with_(data.people[0], stairs_sensitivity=0.9)
    result = filter_places(tower.model_copy(update={"people": (sensitive,)}))
    assert RejectionCode.STAIRS in reasons(result, "muzeum_miejskie")
    ok = with_(sensitive, stairs_sensitivity=0.89)
    result = filter_places(tower.model_copy(update={"people": (ok,)}))
    assert RejectionCode.STAIRS not in reasons(result, "muzeum_miejskie")


def replace_place(
    data: PlanningInput, place: PlaceRead, **changes: object
) -> PlanningInput:
    changed = place.model_copy(update=changes)
    return data.model_copy(
        update={
            "places": tuple(changed if p.id == place.id else p for p in data.places)
        }
    )


def test_segment_over_one_and_a_half_times_s_rejects(museum: PlaceRead) -> None:
    data = planning_input(reference())
    walker = with_(data.people[0], segment_km=1.0, stairs_sensitivity=0.0)
    far = replace_place(data, museum, segment_km=1.6)
    result = filter_places(far.model_copy(update={"people": (walker,)}))
    assert RejectionCode.SEGMENT in reasons(result, "muzeum_miejskie")
    edge = replace_place(data, museum, segment_km=1.5)
    result = filter_places(edge.model_copy(update={"people": (walker,)}))
    assert RejectionCode.SEGMENT not in reasons(result, "muzeum_miejskie")


def test_closed_on_every_day_of_the_trip(data: PlanningInput) -> None:
    only_monday = data.model_copy(
        update={
            "trip": data.trip.model_copy(
                update={"days": (data.trip.days[0].replace(day=5),)}
            )
        }
    )
    assert only_monday.trip.days[0].weekday() == 0
    result = filter_places(only_monday)
    assert RejectionCode.CLOSED in reasons(result, "muzeum_miejskie")


def test_open_on_one_day_is_enough(data: PlanningInput) -> None:
    result = filter_places(data)
    assert RejectionCode.CLOSED not in reasons(result, "muzeum_miejskie")


def test_does_not_fit_the_day_window(data: PlanningInput) -> None:
    short = data.trip.model_copy(
        update={"day_end": data.trip.day_start.replace(hour=10)}
    )
    result = filter_places(data.model_copy(update={"trip": short}))
    assert RejectionCode.NO_FIT in reasons(result, "hevelianum")


def test_must_blocked_reports_rejected_must_places(data: PlanningInput) -> None:
    sea = place_id("restauracja_morska")
    museum_id = place_id("muzeum_miejskie")
    result = filter_places(
        data.model_copy(update={"must": frozenset({sea, museum_id})})
    )
    assert result.must_blocked == (sea,)


def test_accepted_places_have_no_reasons_and_lodging_is_not_a_candidate(
    data: PlanningInput,
) -> None:
    result = filter_places(data)
    accepted = {p.id for p in result.accepted}
    assert accepted
    assert not accepted & {r.place_id for r in result.rejections}
    assert all(p.category.value != "lodging" for p in result.accepted)
    assert {key_of(p) for p in result.accepted} <= set(CATALOG)


def test_reference_family_tower_is_rejected_for_grandma(data: PlanningInput) -> None:
    result = filter_places(data)
    assert RejectionCode.STAIRS in reasons(result, "wieza_widokowa")


def test_result_is_deterministic(data: PlanningInput) -> None:
    assert filter_places(data) == filter_places(data)
