"""Lint inputs built from a trip: name matching, the context and the stored plan."""

import uuid
from datetime import date

import pytest

from tests.fixtures.city import places
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.demo.logic.dataset import DEMO_TRIPS
from tuttitrip.planning.linter.logic import trip_lint
from tuttitrip.planning.linter.logic.rules import lint
from tuttitrip.planning.linter.schemas import LintRequest
from tuttitrip.planning.plans.logic.sample_plan import sample_plan

CATALOG = list(places().values())


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("Park Oliwski", "Park Oliwski"),
        ("park   OLIWSKI!", "Park Oliwski"),
        ("Centrum Nauki Hevelianum w Gdańsku", "Centrum Nauki Hevelianum"),
        ("Hevelianum", "Centrum Nauki Hevelianum"),
        ("Ogrod zoologiczny", "Ogród zoologiczny"),
        ("Półwysep Wschodni", "Półwysep Wschodni"),
    ],
)
def test_names_are_matched_to_the_catalog(typed: str, expected: str) -> None:
    found = trip_lint.match_place(typed, CATALOG)
    assert found is not None
    assert found.name == expected


@pytest.mark.parametrize(
    "typed",
    ["Podwodny Park Wodny", "Bar", "", "!!!", "Restauracja"],
)
def test_unsure_names_stay_unrecognised(typed: str) -> None:
    # "Restauracja" is contained in two catalog names: a guess would be wrong.
    assert trip_lint.match_place(typed, CATALOG) is None


def test_context_carries_the_people_the_zone_and_the_budget() -> None:
    planning = planning_input(reference(), lodging=False)
    names = {p.id: f"Osoba {n}" for n, p in enumerate(planning.people)}
    context = trip_lint.context_of(planning, names)
    assert [p.name for p in context.people] == list(names.values())
    assert context.timezone == planning.trip.timezone
    assert context.budget == planning.trip.budget_to
    assert context.flex_pct == planning.trip.flex_pct
    assert context.lodging is None
    assert context.lunch is None


def test_stored_plan_becomes_a_lint_plan_with_group_costs() -> None:
    planning = planning_input(reference(), lodging=False)
    plan = sample_plan(uuid.uuid4())
    days = planning.trip.days
    out = trip_lint.plan_of(plan, days, len(planning.people))
    assert len(out.days) == len(plan.days)
    first, stop = out.days[0].items[0], plan.days[0].items[0]
    assert first.place_id == stop.place_id
    assert first.start == stop.start
    assert first.cost == (stop.price_base or 0) * len(planning.people)


def test_linting_the_stored_plan_gives_a_report_with_every_rule() -> None:
    planning = planning_input(reference(), lodging=False)
    plan = sample_plan(uuid.uuid4())
    request = LintRequest(
        plan=trip_lint.plan_of(plan, planning.trip.days, len(planning.people)),
        context=trip_lint.context_of(planning, {}),
    )
    report = lint(request.plan, request.context)
    assert len(report.results) >= 1
    assert report.score >= 0


def test_demo_chatbot_text_matches_its_structure() -> None:
    family = next(t for t in DEMO_TRIPS if t.name == "Warszawa z rodziną")
    today = date(2026, 10, 4)
    plan = family.chatbot_plan(today)
    text = family.chatbot_text(today)
    assert plan is not None
    assert text is not None
    assert text.count("Dzień ") == len(plan.days) == 3
    assert all(s.name in text for d in plan.days for s in d.items)
    assert plan.days[0].day == date(2026, 10, 25)
