"""Upgrades of a plan that costs less than B_od (backend#101)."""

from dataclasses import replace
from decimal import Decimal
from uuid import UUID

import pytest

import tuttitrip.planning.logic.upgrades as upgrades_module
from tests.fixtures.city import place_id
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.planning.logic.plan_group import GroupPlan, plan_group
from tuttitrip.planning.logic.upgrades import MAX_UPGRADES, Upgrade, find_upgrades
from tuttitrip.planning.plans.schemas import UpgradeKind
from tuttitrip.planning.schemas import PlanningInput

BASE = planning_input(reference(), lodging=False)
WIDE = BASE.model_copy(
    update={
        "trip": BASE.trip.model_copy(
            update={"budget_from": Decimal(3000), "budget_to": Decimal(4000)}
        )
    }
)
EVALUATIONS = 30  # a search cut short leaves a plan with room to improve
SEA = place_id("restauracja_morska")  # vetoed by one person of the family


@pytest.fixture(scope="module")
def unfinished() -> GroupPlan:
    return plan_group(WIDE, max_evaluations=EVALUATIONS)


@pytest.fixture(scope="module")
def converged() -> GroupPlan:
    return plan_group(WIDE)


def ids(found: tuple[Upgrade, ...]) -> set[UUID]:
    return {u.place_id for u in found}


def test_a_plan_below_b_od_gets_upgrades_with_cost_and_gain(
    unfinished: GroupPlan,
) -> None:
    found = find_upgrades(WIDE, unfinished)
    assert 0 < len(found) <= MAX_UPGRADES
    for upgrade in found:
        assert upgrade.d_j > 0
        assert upgrade.extra_cost >= 0
        assert upgrade.cost == unfinished.plan.cost.total + upgrade.extra_cost
        assert upgrade.cost <= WIDE.trip.budget_from
        assert upgrade.day >= 1
    assert [u.d_j for u in found] == sorted((u.d_j for u in found), reverse=True)
    assert len({(u.kind, u.place_id, u.replaces_place_id) for u in found}) == len(found)


def test_an_upgrade_reports_the_change_of_min_r(unfinished: GroupPlan) -> None:
    found = find_upgrades(WIDE, unfinished)
    assert any(u.d_min_r != 0 for u in found)
    assert all(-1 <= u.d_min_r <= 1 for u in found)


def test_no_real_upgrade_gives_an_empty_list(converged: GroupPlan) -> None:
    # The solver already added everything that raises J.
    assert find_upgrades(WIDE, converged) == ()


def test_a_plan_at_or_above_b_od_gets_none(unfinished: GroupPlan) -> None:
    exact = WIDE.trip.model_copy(update={"budget_from": unfinished.plan.cost.total})
    assert find_upgrades(WIDE.model_copy(update={"trip": exact}), unfinished) == ()


def test_the_room_below_b_od_limits_every_upgrade(unfinished: GroupPlan) -> None:
    room = Decimal(60)
    tight = WIDE.trip.model_copy(
        update={"budget_from": unfinished.plan.cost.total + room}
    )
    found = find_upgrades(WIDE.model_copy(update={"trip": tight}), unfinished)
    assert found
    assert all(u.extra_cost <= room for u in found)


def test_a_vetoed_place_is_never_an_upgrade(
    unfinished: GroupPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upgrades_module, "MAX_UPGRADES", 1000)
    assert SEA not in ids(find_upgrades(WIDE, unfinished))
    # Without the veto the same place is a candidate, so the filter is the veto.
    free: PlanningInput = WIDE.model_copy(
        update={
            "people": tuple(
                p.model_copy(update={"vetoes": frozenset()}) for p in WIDE.people
            )
        }
    )
    assert SEA in ids(
        find_upgrades(free, plan_group(free, max_evaluations=EVALUATIONS))
    )


def test_a_blocked_place_is_never_an_upgrade(
    unfinished: GroupPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upgrades_module, "MAX_UPGRADES", 1000)
    candidate = next(iter(ids(find_upgrades(WIDE, unfinished))))
    blocked = WIDE.model_copy(update={"blocked": frozenset({candidate})})
    assert candidate not in ids(find_upgrades(blocked, unfinished))


def test_a_dearer_place_of_the_same_category_replaces_a_cheaper_one(
    converged: GroupPlan,
) -> None:
    indian, milk_bar = place_id("restauracja_indyjska"), place_id("bar_mleczny")
    days = tuple(
        replace(
            d,
            schedule=replace(
                d.schedule,
                visits=tuple(
                    replace(
                        v, place_id=milk_bar if v.place_id == indian else v.place_id
                    )
                    for v in d.schedule.visits
                ),
            ),
        )
        for d in converged.plan.days
    )
    cheap = replace(converged, plan=replace(converged.plan, days=days))
    found = find_upgrades(WIDE, cheap)
    swap = next(
        u
        for u in found
        if u.kind is UpgradeKind.REPLACE and u.replaces_place_id == milk_bar
    )
    assert swap.place_id == indian
    assert swap.extra_cost > 0
    assert swap.d_j > 0
