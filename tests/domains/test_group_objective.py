"""E5 (group goal J, soft violations, tie-break) and the Jain measure: unit tests."""

import math
from decimal import Decimal
from uuid import UUID

import pytest

from tests.fixtures.city import place_id, places
from tests.fixtures.expected import REFERENCE_PEOPLE, REFERENCE_PLAN
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.planning.fairness.logic.measure import build_report, jain, min_r
from tuttitrip.planning.fairness.logic.objective import (
    PersonOutcome,
    group_objective,
    rank_key,
)
from tuttitrip.planning.fairness.logic.violations import (
    PersonViolation,
    days_without_own_place,
    effective_floor,
    floor_term,
    min_tag_count,
    person_violation,
    tag_shortfalls,
)
from tuttitrip.planning.fairness.logic.welfare import (
    phi,
    weighted_log_welfare,
    welfare,
)
from tuttitrip.planning.fairness.schemas import ConflictCode
from tuttitrip.planning.schemas import DayPlan, PlanningInput

CATALOG = places()
BY_ID = {p.id: p for p in CATALOG.values()}
DATA = planning_input(reference())
TY, KASIA, TOMEK, BABCIA = DATA.people
CANDIDATES = [p for p in DATA.places if p.category.value != "lodging"]


def plan(*days: tuple[str, ...]) -> list[DayPlan]:
    return [DayPlan(place_ids=tuple(place_id(k) for k in day)) for day in days]


REFERENCE = plan(*REFERENCE_PLAN)
NO_INDIAN = plan(
    ("muzeum_miejskie",),
    ("hevelianum", "bar_mleczny", "kawiarnia_w_ogrodzie"),
    ("park_oliwski", "planszowki", "pizzeria"),
)


# --- W: welfare with alpha -----------------------------------------------------


def test_balanced_plan_wins_for_nash() -> None:
    plan_a = welfare([(90, 1), (10, 1)])
    plan_b = welfare([(55, 1), (45, 1)])
    assert plan_a == pytest.approx(math.log(91) + math.log(11))
    assert plan_b == pytest.approx(math.log(56) + math.log(46))
    assert plan_b > plan_a


def test_phi_hand_computed_for_alpha_0_2_and_3() -> None:
    assert phi(9, 0) == pytest.approx(10)  # (1 + u)
    assert phi(9, 2) == pytest.approx(-0.1)  # -1 / (1 + u)
    assert phi(9, 3) == pytest.approx(-0.005)  # (1 + u)^-2 / -2
    assert phi(9, 1) == pytest.approx(math.log(10))


def test_alpha_two_hand_computed_welfare() -> None:
    # w = (2, 1), u = (9, 19): 2 * (-0.1) + (-0.05) = -0.25.
    assert welfare([(9, 2), (19, 1)], alpha=2) == pytest.approx(-0.25)


def test_alpha_outside_the_slider_is_rejected() -> None:
    with pytest.raises(ValueError, match="alpha"):
        phi(10, 3.5)
    with pytest.raises(ValueError, match="alpha"):
        phi(10, -0.1)


def test_weighted_log_welfare_is_welfare_at_alpha_one() -> None:
    people = [(30, 2.0), (50, 1.0)]
    assert weighted_log_welfare(people) == welfare(people, 1.0)


@pytest.mark.parametrize("alpha", [0.0, 0.5, 1.0, 2.0, 3.0])
@pytest.mark.parametrize(
    ("before", "after"),
    [((90, 10), (80, 20)), ((70, 30), (50, 50)), ((60, 40), (45, 55))],
)
def test_transfer_to_the_worse_off_never_lowers_welfare(
    alpha: float, before: tuple[float, float], after: tuple[float, float]
) -> None:
    # Pigou-Dalton: same total, smaller gap.
    assert (
        welfare([(u, 1) for u in after], alpha)
        >= welfare([(u, 1) for u in before], alpha) - 1e-12
    )


def test_welfare_does_not_depend_on_the_order_of_people() -> None:
    people = [(12.5, 1.0), (88.1, 2.0), (47.3, 1.0), (3.9, 2.0)]
    shuffled = [people[2], people[0], people[3], people[1]]
    assert welfare(people, 1.7) == welfare(shuffled, 1.7)


# --- k: tag minima ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("points", "k"),
    [(0, 0), (3, 0), (4, 1), (5, 1), (6, 1), (7, 2), (8, 2), (9, 2), (10, 3)],
)
def test_minimum_count_on_integer_points(points: int, k: int) -> None:
    # k = 1 + floor((points - 4) * 2 / 6); 7 points is exactly 2 (no float drift).
    assert min_tag_count(points) == k


def test_tomek_needs_one_indian_place_and_the_plan_has_it() -> None:
    assert TOMEK.pool.food == 4
    (short,) = tag_shortfalls(TOMEK, REFERENCE, BY_ID, CANDIDATES)
    assert (short.required, short.have, short.term) == (1, 1, 0)


def test_missing_minimum_adds_one_to_v_and_to_the_report() -> None:
    def violation(days: list[DayPlan]) -> PersonViolation:
        return person_violation(
            TOMEK,
            utility=50,
            floor_eff=0.0,
            days=days,
            places=BY_ID,
            candidates=CANDIDATES,
        )

    base, missing = violation(REFERENCE), violation(NO_INDIAN)
    assert base.total == pytest.approx(0)
    # Indian is also Tomek's own place on day 1: +1 (minimum) + 0.5 (that day).
    assert missing.total - base.total == pytest.approx(1.5)
    assert missing.tags[0].term == pytest.approx(1)


def test_minimum_is_capped_by_availability() -> None:
    no_indian_on_offer = [p for p in CANDIDATES if p.cuisine is None]
    assert tag_shortfalls(TOMEK, NO_INDIAN, BY_ID, no_indian_on_offer) == ()


# --- own places and floors -------------------------------------------------------


def test_days_without_own_place_count_empty_days() -> None:
    assert days_without_own_place(TOMEK, REFERENCE, BY_ID) == 0
    assert days_without_own_place(TOMEK, plan((), ("hevelianum",)), BY_ID) == 1
    # Ty only likes the museum (m = 0.3 * cos + 0.7 with a vote), not the zoo.
    assert days_without_own_place(TY, plan(("zoo",)), BY_ID) == 1


def test_floor_term_is_relative_shortfall() -> None:
    assert floor_term(20, 30) == pytest.approx(1 / 3)
    assert floor_term(30, 30) == pytest.approx(0)
    assert floor_term(0, 0) == pytest.approx(0)


@pytest.mark.parametrize("key", REFERENCE_PEOPLE)
def test_effective_floor_matches_the_spec_table(key: str) -> None:
    row = REFERENCE_PEOPLE[key]
    person = {"ty": TY, "kasia": KASIA, "tomek": TOMEK, "babcia": BABCIA}[key]
    # f_eff = min(f, 0.6 * u*); the table prints it to one decimal.
    assert effective_floor(person.floor, row.u_star) == pytest.approx(
        row.floor, abs=0.05
    )


def test_effective_floor_never_exceeds_sixty_percent_of_alone() -> None:
    assert effective_floor(80, 50) == pytest.approx(30)
    assert effective_floor(20, 50) == pytest.approx(20)
    assert effective_floor(30, 0) == pytest.approx(0)


# --- J, ties and permutation ---------------------------------------------------------


def outcomes(
    utilities: tuple[float, ...], floors: tuple[float, ...]
) -> list[PersonOutcome]:
    return list(map(PersonOutcome, DATA.people, utilities, floors, strict=True))


def objective(items: list[PersonOutcome], days: list[DayPlan], alpha: float = 1.0):  # ruff: ignore[missing-return-type-undocumented-public-function]
    return group_objective(
        items, days=days, places=BY_ID, candidates=CANDIDATES, alpha=alpha
    )


def test_j_is_welfare_minus_1000_times_violation() -> None:
    items = outcomes((50, 20, 40, 60), (30, 35, 26.3, 30))
    result = objective(items, NO_INDIAN)
    # Kasia (u 20 vs f 35): 15/35. Tomek: 1 (minimum) + 0.5 (no own place on day 1).
    kasia_floor = 15 / 35
    assert result.violation >= kasia_floor + 1.5 - 1e-9
    assert result.value == pytest.approx(result.welfare - 1000 * result.violation)
    clean = objective(outcomes((50, 40, 40, 60), (30, 35, 26.3, 30)), REFERENCE)
    # Only Kasia has no own place on day 1 (museum and an Indian lunch): 1/2.
    assert clean.violation == pytest.approx(0.5)
    assert clean.value == pytest.approx(clean.welfare - 500)


def test_penalty_dominates_welfare_differences() -> None:
    rich = objective(outcomes((100, 100, 100, 100), (0, 0, 0, 0)), NO_INDIAN)
    poor = objective(outcomes((1, 1, 1, 1), (0, 0, 0, 0)), REFERENCE)
    assert poor.value > rich.value


def test_j_does_not_depend_on_the_order_of_people() -> None:
    items = outcomes((50, 20, 40, 60), (30, 35, 26.3, 30))
    shuffled = [items[2], items[0], items[3], items[1]]
    a, b = objective(items, NO_INDIAN), objective(shuffled, NO_INDIAN)
    assert a.value == b.value
    assert [v.person_id for v in a.violations] == [v.person_id for v in b.violations]


def test_for_one_person_the_weight_does_not_change_the_ranking() -> None:
    plans = [REFERENCE, NO_INDIAN]
    ranking = []
    for weight in (1.0, 2.0, 3.0):
        solo = TY.model_copy(update={"weight": weight})
        scored = [
            group_objective(
                [PersonOutcome(solo, u, 0)],
                days=days,
                places=BY_ID,
                candidates=CANDIDATES,
            ).value
            for u, days in ((70, REFERENCE), (55, NO_INDIAN))
        ]
        ranking.append(scored.index(max(scored)))
    assert ranking == [0, 0, 0]
    assert plans  # both plans were compared


def test_tie_break_is_j_then_lower_cost_then_ids() -> None:
    items = outcomes((50, 50, 50, 50), (0, 0, 0, 0))
    same = objective(items, REFERENCE)
    ids = [place_id("zoo"), place_id("park_oliwski")]
    cheap = rank_key(same, Decimal(100), ids)
    dear = rank_key(same, Decimal(200), ids)
    assert cheap < dear
    zoo, park = place_id("zoo"), place_id("park_oliwski")
    low, high = sorted([zoo, park], key=str)
    assert rank_key(same, Decimal(100), [low]) < rank_key(same, Decimal(100), [high])
    better = objective(outcomes((60, 60, 60, 60), (0, 0, 0, 0)), REFERENCE)
    assert rank_key(better, Decimal(999), ids) < cheap


def test_noise_below_the_rounding_does_not_break_a_tie() -> None:
    items = outcomes((50, 50, 50, 50), (0, 0, 0, 0))
    a = objective(items, REFERENCE)
    noisy = type(a)(a.welfare, a.violation, a.value + 1e-12, a.violations)
    ids = [place_id("zoo")]
    assert rank_key(a, Decimal(1), ids) == rank_key(noisy, Decimal(1), ids)


# --- measures and the report ---------------------------------------------------------


def test_jain_index() -> None:
    assert jain([1, 1, 1]) == pytest.approx(1)
    assert jain([1, 0]) == pytest.approx(0.5)
    assert jain([0.99, 0.91, 0.94, 0.87]) == pytest.approx(0.998, abs=0.001)
    assert jain([0.8]) == pytest.approx(1)
    assert jain([]) == pytest.approx(1)
    assert jain([0, 0]) == pytest.approx(1)


def test_min_r() -> None:
    assert min_r([0.99, 0.91, 0.94, 0.87]) == pytest.approx(0.87)
    assert min_r([]) == pytest.approx(1)


def test_report_lists_every_miss_with_its_cause() -> None:
    items = outcomes((50, 20, 40, 60), (30, 35, 26.3, 30))
    report = build_report(objective(items, NO_INDIAN))
    assert report.floors_missed == [KASIA.id]
    codes = {(c.person_id, c.code) for c in report.conflicts}
    assert (KASIA.id, ConflictCode.FLOOR) in codes
    assert (TOMEK.id, ConflictCode.TAG_MINIMUM) in codes
    assert (TOMEK.id, ConflictCode.OWN_PLACE) not in {
        c for c in codes if c[0] != TOMEK.id
    }
    tag = next(c for c in report.conflicts if c.code is ConflictCode.TAG_MINIMUM)
    assert (tag.tag, tag.missing) == ("indian", 1)
    assert report.violation > 0


def test_reference_plan_misses_only_kasias_first_day() -> None:
    items = outcomes((50, 40, 40, 60), (30, 35, 26.3, 30))
    report = build_report(objective(items, REFERENCE))
    assert report.floors_missed == []
    (conflict,) = report.conflicts
    assert (conflict.person_id, conflict.code) == (KASIA.id, ConflictCode.OWN_PLACE)
    assert conflict.missing == 1
    assert report.violation == pytest.approx(0.5)


def test_ids_are_uuids_in_the_report() -> None:
    report = build_report(
        objective(outcomes((50, 20, 40, 60), (30, 35, 26.3, 30)), NO_INDIAN)
    )
    assert all(isinstance(c.person_id, UUID) for c in report.conflicts)
    assert isinstance(DATA, PlanningInput)
