"""Budget of each day, the overrun and the proposal for the rest (backend#89)."""

import datetime as dt
from decimal import Decimal

import pytest

from tests.domains.test_planning_properties import assignment_of
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.expenses.schemas import ExpenseCategory, ExpenseDayTotal
from tuttitrip.planning.budget.logic.daily_budget import (
    DayBudget,
    budget_left,
    day_budgets,
    last_overrun,
    rest_of_trip,
    spent_per_day,
)
from tuttitrip.planning.budget.logic.proposal import propose_rest
from tuttitrip.planning.logic.budget_consent import plan_with_consent
from tuttitrip.planning.logic.params import DEFAULT_PARAMS
from tuttitrip.planning.logic.solver import Assignment
from tuttitrip.planning.schemas import PlanningInput

FRI, SAT, SUN = (dt.date(2026, 10, 9), dt.date(2026, 10, 10), dt.date(2026, 10, 11))
DAYS = [FRI, SAT, SUN]


def spent(
    day: dt.date, amount: int, category: ExpenseCategory | None
) -> ExpenseDayTotal:
    return ExpenseDayTotal(spent_on=day, category=category, amount=Decimal(amount))


def budgets(
    *totals: ExpenseDayTotal,
    day_from: Decimal | None = None,
    day_to: Decimal | None = None,
) -> list[DayBudget]:
    return day_budgets(
        DAYS,
        total_from=Decimal(900),
        total_to=Decimal(1500),
        flex_pct=10,
        day_from=day_from,
        day_to=day_to,
        totals=totals,
    )


# --- the budget of a day -----------------------------------------------------------


def test_the_trip_budget_is_divided_by_the_days_with_the_margin() -> None:
    first = budgets()[0]
    assert (first.budget_from, first.budget_to) == (Decimal(300), Decimal(500))
    assert first.budget_max == Decimal(550)  # B_do * (1 + flex)
    assert (first.spent, first.remaining, first.remaining_max) == (0, 500, 550)
    assert not first.over_budget
    assert [b.index for b in budgets()] == [1, 2, 3]


def test_a_day_range_of_the_trip_beats_the_division() -> None:
    day = budgets(day_from=Decimal(100), day_to=Decimal(400))[1]
    assert (day.budget_from, day.budget_to, day.budget_max) == (100, 400, 440)


def test_the_division_rounds_to_the_cent() -> None:
    day = day_budgets(DAYS, total_from=Decimal(100), total_to=Decimal(100), flex_pct=0)[
        0
    ]
    assert day.budget_to == Decimal("33.33")


def test_plan_expenses_count_and_souvenirs_lodging_and_other_do_not() -> None:
    day = budgets(
        spent(FRI, 120, ExpenseCategory.FOOD),
        spent(FRI, 30, ExpenseCategory.TRANSPORT),
        spent(FRI, 50, ExpenseCategory.ACTIVITIES),
        spent(FRI, 10, None),
        spent(FRI, 200, ExpenseCategory.SHOPPING),
        spent(FRI, 300, ExpenseCategory.LODGING),
        spent(FRI, 5, ExpenseCategory.OTHER),
    )[0]
    assert day.spent == Decimal(210)
    assert day.outside_plan == Decimal(505)
    assert not day.over_budget  # the souvenirs did not take the attractions' budget


def test_spent_per_day_adds_the_categories_of_one_day() -> None:
    counted, outside = spent_per_day(
        [
            spent(FRI, 1, ExpenseCategory.FOOD),
            spent(FRI, 2, None),
            spent(SAT, 4, ExpenseCategory.OTHER),
        ]
    )
    assert counted == {FRI: Decimal(3)}
    assert outside == {SAT: Decimal(4)}


def test_a_day_is_over_above_b_do_and_over_max_above_b_max() -> None:
    days = budgets(
        spent(FRI, 500, ExpenseCategory.FOOD),
        spent(SAT, 501, ExpenseCategory.FOOD),
        spent(SUN, 551, ExpenseCategory.FOOD),
    )
    assert [(b.over_budget, b.over_max) for b in days] == [
        (False, False),
        (True, False),
        (True, True),
    ]
    assert days[1].remaining == Decimal(-1)
    assert last_overrun(days) is days[2]
    assert last_overrun(budgets()) is None


def test_the_budget_left_is_reduced_by_what_was_spent_and_never_negative() -> None:
    days = budgets(spent(FRI, 700, ExpenseCategory.FOOD), spent(SAT, 100, None))
    assert budget_left(days, 1, total_from=Decimal(900), total_to=Decimal(1500)) == (
        Decimal(200),
        Decimal(800),
    )
    assert budget_left(days, 2, total_from=Decimal(900), total_to=Decimal(1500)) == (
        Decimal(100),
        Decimal(700),
    )
    broke = budgets(spent(FRI, 2000, None))
    assert budget_left(broke, 1, total_from=Decimal(900), total_to=Decimal(1500)) == (
        Decimal(0),
        Decimal(0),
    )


# --- the rest of the trip -----------------------------------------------------------


def test_the_rest_has_the_remaining_days_the_new_budget_and_no_visited_places() -> None:
    data = planning_input(reference(), lodging=False)
    gone = frozenset(p.id for p in data.places[:3])
    rest = rest_of_trip(
        data.model_copy(
            update={"must": frozenset({data.places[0].id, data.places[5].id})}
        ),
        after=1,
        visited=gone,
        budget_from=Decimal(100),
        budget_to=Decimal(400),
    )
    assert rest.trip.days == data.trip.days[1:]
    assert (rest.trip.budget_from, rest.trip.budget_to) == (100, 400)
    assert not gone & {p.id for p in rest.places}
    assert rest.must == {data.places[5].id}
    assert rest.people == data.people


# --- the proposal ---


def plan_and_rest(
    spent_on_first_day: int,
) -> tuple[PlanningInput, Assignment, Decimal]:
    data = planning_input(reference(), lodging=False)
    full = plan_with_consent(data, DEFAULT_PARAMS)
    planned = assignment_of(full.chosen.plan)
    visited = frozenset(planned[0])
    days = day_budgets(
        list(data.trip.days),
        total_from=data.trip.budget_from,
        total_to=data.trip.budget_to,
        flex_pct=data.trip.flex_pct,
        totals=[spent(data.trip.days[0], spent_on_first_day, ExpenseCategory.FOOD)],
    )
    left_from, left_to = budget_left(
        days, 1, total_from=data.trip.budget_from, total_to=data.trip.budget_to
    )
    rest = rest_of_trip(
        data, after=1, visited=visited, budget_from=left_from, budget_to=left_to
    )
    return rest, planned[1:], left_to


def test_after_an_overrun_the_rest_gets_a_cheaper_plan_with_cost_and_min_r() -> None:
    rest, previous, left_to = plan_and_rest(1100)
    found = propose_rest(rest, DEFAULT_PARAMS, alpha=1.0, assignment=previous)
    assert found is not None
    chosen = found.decision.chosen
    assert chosen.plan.cost.total < found.previous.cost
    assert chosen.plan.cost.total <= left_to * (1 + Decimal(rest.trip.flex_pct) / 100)
    assert found.previous.min_r is not None
    assert 0 <= chosen.min_r <= 1


def test_the_same_expenses_and_data_give_the_same_proposal() -> None:
    rest, previous, _ = plan_and_rest(1100)
    first = propose_rest(rest, DEFAULT_PARAMS, alpha=1.0, assignment=previous)
    second = propose_rest(rest, DEFAULT_PARAMS, alpha=1.0, assignment=previous)
    assert first is not None
    assert second is not None
    assert first.decision.chosen.plan.plan_hash == second.decision.chosen.plan.plan_hash
    assert first.previous == second.previous


def test_nothing_is_proposed_when_the_plan_already_fits_what_is_left() -> None:
    rest, previous, _ = plan_and_rest(1)
    found = propose_rest(rest, DEFAULT_PARAMS, alpha=1.0, assignment=previous)
    assert found is None or found.decision.chosen.plan.cost.total < found.previous.cost


@pytest.mark.parametrize("amount", [1100, 1500])
def test_a_proposal_never_goes_over_the_budget_of_the_rest(amount: int) -> None:
    rest, previous, _ = plan_and_rest(amount)
    found = propose_rest(rest, DEFAULT_PARAMS, alpha=1.0, assignment=previous)
    if found is not None:
        assert found.decision.chosen.plan.cost.total <= rest.trip.budget_max
