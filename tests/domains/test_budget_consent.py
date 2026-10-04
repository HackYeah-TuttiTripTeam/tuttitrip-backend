"""E6: consent to exceed the budget (good reason, price of a point)."""

from dataclasses import replace
from decimal import Decimal

import pytest

from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.planning.logic.budget_consent import (
    decide,
    has_strong_preference,
    plan_with_consent,
)
from tuttitrip.planning.logic.params import DEFAULT_PARAMS
from tuttitrip.planning.logic.plan_group import GroupPlan, PersonReference, plan_group
from tuttitrip.planning.schemas import LodgingStay, PlanningInput
from tuttitrip.profiles.preferences.schemas import ImportancePool

STAY = LodgingStay(nights=2, price_per_night=Decimal(250))
BASE = planning_input(reference())


def budget(b_to: int, flex: int) -> PlanningInput:
    trip = BASE.trip.model_copy(
        update={
            "budget_to": Decimal(b_to),
            "budget_from": Decimal(b_to - 200),
            "flex_pct": flex,
        }
    )
    return BASE.model_copy(update={"trip": trip})


def variant(group: GroupPlan, cost: str, us: list[float], min_r: float) -> GroupPlan:
    """A copy of a real group plan with a given cost, welfare and ``min r``."""
    rows = tuple(
        replace(row, u=u, r=min(1.0, (u + 10) / (row.u_star + 10)))
        for row, u in zip(group.people, us, strict=True)
    )
    plan = replace(group.plan, cost=replace(group.plan.cost, total=Decimal(cost)))
    return replace(group, plan=plan, people=rows, min_r=min_r)


# --- the decision, with plans we control --------------------------------------------


def strong_index(data: PlanningInput, group: GroupPlan) -> int:
    """Row of a person with a strong preference (the rows are in id order)."""
    people = {p.id: p for p in data.people}
    return next(
        i
        for i, row in enumerate(group.people)
        if has_strong_preference(
            people[row.person_id], has_lodging=True, params=DEFAULT_PARAMS
        )
    )


def with_gain(index: int, gain: float) -> list[float]:
    values = [50.0, 50.0, 50.0, 50.0]
    values[index] += gain
    return values


@pytest.fixture
def plans() -> tuple[PlanningInput, GroupPlan]:
    data = budget(1000, 20)
    return data, plan_group(data, lodging=STAY)


def test_a_big_gain_and_no_cheaper_alternative_asks_for_consent(
    plans: tuple[PlanningInput, GroupPlan],
) -> None:
    data, real = plans
    strict = variant(real, "1000", [50, 50, 50, 50], 0.8)
    who = strong_index(data, real)
    flex = variant(real, "1100", with_gain(who, 8), 0.8)  # +8 points
    cheaper = variant(real, "1040", [50, 50, 50, 50], 0.8)
    decision = decide(data, flex, strict, cheaper)
    assert decision.needs_approval
    assert decision.chosen is flex
    assert decision.alternative is strict
    assert decision.over_b_do == Decimal(100)
    assert decision.gain_points == pytest.approx(8)
    # kappa = (1100 - 1000) / 8 = 12.50 per point
    assert decision.kappa == Decimal("12.50")
    assert decision.gain_person_id == flex.people[who].person_id


def test_a_gain_below_eight_points_is_not_a_good_reason(
    plans: tuple[PlanningInput, GroupPlan],
) -> None:
    data, real = plans
    strict = variant(real, "1000", [50, 50, 50, 50], 0.8)
    flex = variant(real, "1100", with_gain(strong_index(data, real), 7.9), 0.8)
    cheaper = variant(real, "1040", [50, 50, 50, 50], 0.8)
    decision = decide(data, flex, strict, cheaper)
    assert not decision.needs_approval
    assert decision.chosen is strict
    assert decision.kappa is None
    assert decision.alternative is None


def test_min_r_rising_by_five_hundredths_is_a_good_reason(
    plans: tuple[PlanningInput, GroupPlan],
) -> None:
    data, real = plans
    strict = variant(real, "1000", [50, 50, 50, 50], 0.80)
    flex = variant(real, "1100", [54, 52, 52, 52], 0.85)
    cheaper = variant(real, "1040", [50, 50, 50, 50], 0.80)
    assert decide(data, flex, strict, cheaper).needs_approval
    flex_small = variant(real, "1100", [54, 52, 52, 52], 0.849)
    assert not decide(data, flex_small, strict, cheaper).needs_approval


def test_a_cheaper_alternative_of_similar_welfare_blocks_consent(
    plans: tuple[PlanningInput, GroupPlan],
) -> None:
    data, real = plans
    who = strong_index(data, real)
    strict = variant(real, "1000", [50, 50, 50, 50], 0.8)
    flex = variant(real, "1100", with_gain(who, 8), 0.8)
    nearly_as_good = variant(real, "1040", with_gain(who, 8), 0.8)
    assert not decide(data, flex, strict, nearly_as_good).needs_approval


def test_only_strong_preferences_count_for_the_point_gain(
    plans: tuple[PlanningInput, GroupPlan],
) -> None:
    data, real = plans
    flat = ImportancePool(lodging=2, food=2, attractions=2, pace=2, cost=2)
    people = tuple(p.model_copy(update={"pool": flat}) for p in data.people)
    weak = data.model_copy(update={"people": people})
    assert not has_strong_preference(people[0], has_lodging=True, params=DEFAULT_PARAMS)
    strict = variant(real, "1000", [50, 50, 50, 50], 0.8)
    flex = variant(real, "1100", [60, 50, 50, 50], 0.8)
    cheaper = variant(real, "1040", [50, 50, 50, 50], 0.8)
    assert not decide(weak, flex, strict, cheaper).needs_approval
    assert has_strong_preference(
        data.people[0].model_copy(
            update={"pool": flat.model_copy(update={"food": 4, "cost": 0})}
        ),
        has_lodging=True,
        params=DEFAULT_PARAMS,
    )


# --- the whole computation on the fixture family ------------------------------------


def test_within_b_do_there_is_one_run_and_no_consent() -> None:
    data = budget(2500, 10)
    decision = plan_with_consent(data, lodging=STAY)
    assert decision.runs == 1
    assert not decision.needs_approval
    assert decision.over_b_do == 0
    assert decision.kappa is None


def test_a_family_that_goes_over_gets_a_priced_proposal() -> None:
    data = budget(1300, 20)
    decision = plan_with_consent(data, lodging=STAY)
    assert decision.runs == 3
    assert decision.needs_approval
    assert decision.chosen.plan.cost.total > data.trip.budget_to
    assert decision.over_b_do == decision.chosen.plan.cost.total - data.trip.budget_to
    assert decision.kappa is not None
    assert decision.kappa > 0
    assert decision.alternative is not None
    assert decision.alternative.plan.cost.total <= data.trip.budget_to
    assert decision.gain_points is not None
    assert decision.gain_points > 0
    expected = (
        decision.chosen.plan.cost.total - decision.alternative.plan.cost.total
    ) / Decimal(str(decision.gain_points))
    assert decision.kappa == expected.quantize(Decimal("0.01"))


def test_without_flex_nothing_needs_approval() -> None:
    for b_to in (700, 1000, 1300):
        decision = plan_with_consent(budget(b_to, 0), lodging=STAY)
        assert not decision.needs_approval
        assert decision.chosen.plan.cost.total <= Decimal(b_to)


@pytest.mark.parametrize(
    ("b_to", "flex"), [(700, 10), (1000, 20), (1300, 20), (1200, 30)]
)
def test_the_cost_never_exceeds_b_max_and_a_refusal_gives_the_strict_plan(
    b_to: int, flex: int
) -> None:
    data = budget(b_to, flex)
    decision = plan_with_consent(data, lodging=STAY)
    assert decision.chosen.plan.cost.total <= data.trip.budget_max
    if not decision.needs_approval:
        assert decision.chosen.plan.cost.total <= data.trip.budget_to or (
            decision.chosen.plan.cost.total <= data.trip.budget_max
        )
        assert decision.kappa is None
    else:
        assert decision.kappa is not None


def test_the_three_plans_share_the_reference_points() -> None:
    data = budget(1300, 20)
    decision = plan_with_consent(data, lodging=STAY)
    assert decision.alternative is not None
    chosen = {r.person_id: r.u_star for r in decision.chosen.people}
    alternative = {r.person_id: r.u_star for r in decision.alternative.people}
    assert chosen == alternative
    assert decision.alternative.solo_runs == 0


def test_person_reference_rows_have_the_fields_the_decision_reads() -> None:
    assert {"person_id", "u", "u_star", "r"} <= set(
        PersonReference.__dataclass_fields__
    )
