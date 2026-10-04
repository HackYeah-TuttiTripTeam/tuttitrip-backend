"""E2 (domain satisfaction) and E3 (welfare of a person): hand-computed unit tests."""

import math
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from tests.fixtures.city import places
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.accommodation.schemas import RequirementStatus
from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.logic.domains import (
    RequirementOutcome,
    attractions_score,
    cost_score,
    food_score,
    lodging_score,
    pace_score,
)
from tuttitrip.planning.logic.welfare_person import (
    domain_scores,
    renormalised_pool,
    welfare,
)
from tuttitrip.planning.schemas import DayPlan, PlanningPerson, PlanningTrip
from tuttitrip.profiles.preferences.schemas import ImportanceDomain, ImportancePool

CATALOG = places()
DOM = ImportanceDomain


def sight(visit_min: int = 90) -> PlaceRead:
    return CATALOG["muzeum_miejskie"].model_copy(
        update={"id": uuid4(), "typical_visit_min": visit_min}
    )


def meal() -> PlaceRead:
    return CATALOG["bar_mleczny"].model_copy(update={"id": uuid4()})


def day(*items: PlaceRead, km: float = 0, minutes: int = 0) -> DayPlan:
    return DayPlan(
        place_ids=tuple(p.id for p in items), distance_km=km, active_min=minutes
    )


def index(*items: PlaceRead) -> dict[UUID, PlaceRead]:
    return {p.id: p for p in items}


def person(pool: ImportancePool | None = None) -> PlanningPerson:
    base = planning_input(reference()).people[0]
    return base.model_copy(
        update={
            "daily_km": 10.0,
            "active_min": 600,
            "pool": pool
            or ImportancePool(lodging=2, food=2, attractions=3, pace=1, cost=2),
        }
    )


def trip() -> PlanningTrip:
    return planning_input(reference()).trip  # 1300 to 1700 zl, flex 10% -> B_max 1870


# --- attractions and food: saturation per day -------------------------------------


def test_attractions_one_visit_hand_computed() -> None:
    a = sight(90)
    # (90 / 90) * 50 / 100 = 0.5; 100 * (1 - exp(-0.6 * 0.5)) = 25.918.
    score = attractions_score([day(a)], index(a), {a.id: 50})
    assert score == pytest.approx(25.9182, abs=1e-4)


def test_attractions_scale_with_visit_time() -> None:
    a = sight(60)
    score = attractions_score([day(a)], index(a), {a.id: 50})
    assert score == pytest.approx(100 * (1 - math.exp(-0.6 * (60 / 90) * 0.5)))


def test_attractions_are_averaged_over_all_days_including_empty() -> None:
    a = sight()
    score = attractions_score([day(a), day()], index(a), {a.id: 50})
    assert score == pytest.approx(25.9182 / 2, abs=1e-4)


def test_two_attractions_in_one_day_lose_to_one_per_day() -> None:
    a, b = sight(), sight()
    u = {a.id: 50.0, b.id: 50.0}
    crowded = attractions_score([day(a, b), day()], index(a, b), u)
    spread = attractions_score([day(a), day(b)], index(a, b), u)
    assert crowded == pytest.approx(45.1188 / 2, abs=1e-4)
    assert spread == pytest.approx(25.9182, abs=1e-4)
    assert spread > crowded


def test_food_hand_computed_and_domains_are_separate() -> None:
    f, a = meal(), sight()
    u = {f.id: 80.0, a.id: 50.0}
    # 100 * (1 - exp(-1.2 * 0.8)) = 61.711; the museum does not count as food.
    assert food_score([day(f, a)], index(f, a), u) == pytest.approx(61.7107, abs=1e-4)
    assert attractions_score([day(f, a)], index(f, a), u) == pytest.approx(
        25.9182, abs=1e-4
    )


def test_no_food_gives_zero() -> None:
    a = sight()
    assert food_score([day(a)], index(a), {a.id: 50}) == pytest.approx(0)


# --- pace ---------------------------------------------------------------------------


def test_pace_hand_computed() -> None:
    p = person()  # D = 10 km, A = 600 min
    days = [day(km=15, minutes=900), day(km=5, minutes=300)]
    # day 1: 0.5 * 5 / 10 + 0.5 * 300 / 600 = 0.5; day 2: 0; mean 0.25.
    assert pace_score(days, p) == pytest.approx(75)


def test_pace_within_limits_is_full_and_floors_at_zero() -> None:
    p = person()
    assert pace_score([day(km=10, minutes=600)], p) == pytest.approx(100)
    assert pace_score([day(km=100, minutes=6000)], p) == pytest.approx(0)


# --- cost ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cost", "expected"),
    [
        ("1000", 100),
        ("1300", 100),
        ("1500", 80),
        ("1700", 60),
        ("1785", 30),  # halfway between B_do 1700 and B_max 1870
        ("1870", 0),
        ("2500", 0),
    ],
)
def test_cost_score_piecewise(cost: str, expected: float) -> None:
    assert cost_score(Decimal(cost), trip()) == pytest.approx(expected)


def test_cost_score_without_flex_drops_to_zero_above_b_do() -> None:
    rigid = trip().model_copy(update={"flex_pct": 0})
    assert cost_score(Decimal(1700), rigid) == pytest.approx(60)
    assert cost_score(Decimal("1700.01"), rigid) == pytest.approx(0)


def test_cost_score_with_a_single_budget_figure() -> None:
    flat = trip().model_copy(
        update={"budget_from": Decimal(1700), "budget_to": Decimal(1700)}
    )
    assert cost_score(Decimal(1700), flat) == pytest.approx(100)


# --- lodging ------------------------------------------------------------------------

MET, UNC, UNMET = (
    RequirementStatus.MET,
    RequirementStatus.UNCONFIRMED,
    RequirementStatus.UNMET,
)


def test_lodging_hard_product_times_soft_mean() -> None:
    outcomes = [
        RequirementOutcome(hard=True, status=MET),
        RequirementOutcome(hard=False, status=MET),
        RequirementOutcome(hard=False, status=UNC),
        RequirementOutcome(hard=False, status=UNMET),
    ]
    assert lodging_score(outcomes) == pytest.approx(100 * (1 + 0.4 + 0) / 3)


def test_lodging_hard_unconfirmed_and_unmet() -> None:
    assert lodging_score([RequirementOutcome(hard=True, status=UNC)]) == pytest.approx(
        40
    )
    both = [
        RequirementOutcome(hard=True, status=MET),
        RequirementOutcome(hard=True, status=UNC),
    ]
    assert lodging_score(both) == pytest.approx(40)
    assert lodging_score(
        [RequirementOutcome(hard=True, status=UNMET)]
    ) == pytest.approx(0)
    assert lodging_score(
        [RequirementOutcome(hard=False, status=UNMET)]
    ) == pytest.approx(0)


def test_lodging_without_requirements_is_full() -> None:
    assert lodging_score([]) == pytest.approx(100)


# --- E3 welfare ---------------------------------------------------------------------


def two_domain_person() -> PlanningPerson:
    return person(ImportancePool(lodging=0, food=0, attractions=5, pace=5, cost=0))


def scores(**q: float) -> dict[ImportanceDomain, float]:
    base = dict.fromkeys(DOM, 0.0)
    return base | {DOM(k): v for k, v in q.items()}


def test_welfare_example_of_the_spec() -> None:
    p = two_domain_person()
    lopsided = welfare(p, scores(attractions=100, pace=0), has_lodging=True)
    even = welfare(p, scores(attractions=50, pace=50), has_lodging=True)
    assert lopsided == pytest.approx(math.sqrt(101) - 1)
    assert lopsided == pytest.approx(9.05, abs=0.005)
    assert even == pytest.approx(50)


def test_domain_with_no_points_does_not_change_welfare() -> None:
    p = two_domain_person()
    low = welfare(p, scores(attractions=50, pace=50, cost=0, food=0), has_lodging=True)
    high = welfare(
        p, scores(attractions=50, pace=50, cost=100, food=90), has_lodging=True
    )
    assert low == pytest.approx(high)


def test_welfare_stays_in_range() -> None:
    p = person()
    assert welfare(p, dict.fromkeys(DOM, 0.0), has_lodging=True) == pytest.approx(0)
    assert welfare(p, dict.fromkeys(DOM, 100.0), has_lodging=True) <= 100


def test_pool_is_renormalised_without_lodging() -> None:
    p = person(ImportancePool(lodging=4, food=2, attractions=2, pace=1, cost=1))
    shares = renormalised_pool(p, has_lodging=False)
    assert DOM.LODGING not in shares
    assert shares == pytest.approx(
        {DOM.FOOD: 1 / 3, DOM.ATTRACTIONS: 1 / 3, DOM.PACE: 1 / 6, DOM.COST: 1 / 6}
    )
    assert sum(shares.values()) == pytest.approx(1)
    with_lodging = renormalised_pool(p, has_lodging=True)
    assert with_lodging[DOM.LODGING] == pytest.approx(0.4)


def test_pool_only_on_inactive_lodging_falls_back_to_equal_shares() -> None:
    p = person(ImportancePool(lodging=10, food=0, attractions=0, pace=0, cost=0))
    shares = renormalised_pool(p, has_lodging=False)
    assert shares == pytest.approx(dict.fromkeys(shares, 0.25))


def test_domain_scores_not_applicable_without_nights() -> None:
    p = person(ImportancePool(lodging=4, food=2, attractions=2, pace=1, cost=1))
    q = dict.fromkeys(DOM, 40.0)
    result = domain_scores(p, q, has_lodging=False)
    assert result.lodging is None
    assert not result.lodging_applicable
    changed = domain_scores(p, q | {DOM.LODGING: 99.0}, has_lodging=False)
    assert changed.welfare == pytest.approx(result.welfare)
    assert domain_scores(p, q, has_lodging=True).lodging_applicable


def test_domain_scores_are_rounded_to_four_places() -> None:
    p = two_domain_person()
    result = domain_scores(
        p, scores(attractions=33.333333333, pace=66.666666666), has_lodging=True
    )
    assert result.attractions == pytest.approx(33.3333)
    assert round(result.attractions, 4) == result.attractions
    assert round(result.welfare, 4) == result.welfare
