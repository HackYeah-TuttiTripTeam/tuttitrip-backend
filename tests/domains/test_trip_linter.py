"""Linter of a trip: our plan has no violations, a pasted plan is read carefully."""

import uuid
from datetime import date, time
from decimal import Decimal

import pytest

from tests.domains.test_plan_read_model import as_read
from tests.fixtures.city import place_id
from tests.fixtures.personas import reference_family
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.planning.linter.logic.rules import lint
from tuttitrip.planning.logic.plan_group import plan_group
from tuttitrip.planning.plans.logic.read_model import build_content
from tuttitrip.planning.trip_linter.logic.context import lint_context
from tuttitrip.planning.trip_linter.logic.paste_lint import paste_to_lint
from tuttitrip.planning.trip_linter.logic.plan_lint import plan_to_lint
from tuttitrip.shared.jobs.contracts import ParsePastedPlanOutput

DAY_1 = date(2026, 10, 9)
KNOWN = {place_id("muzeum_miejskie"), place_id("park")}


def test_the_solver_plan_of_the_demo_family_has_no_violations() -> None:
    # The scene of the pitch: ours is 0, a chatbot's plan has a number. If the
    # linter and the solver ever disagree about a rule, this fails first.
    scenario = reference()
    data = planning_input(scenario, lodging=False)
    group = plan_group(data)
    names = {p.id: p.key for p in reference_family().people}
    plan = as_read(build_content(data, group, names), group.plan.plan_hash)
    wheelchair = [
        p.id for p in reference_family().people if p.preferences.constraints.wheelchair
    ]
    report = lint(
        plan_to_lint(plan, data.trip.days[0]), lint_context(data, names, wheelchair)
    )
    assert [r.rule for r in report.results].count("budget") == 1
    assert report.count == 0, [f for r in report.results for f in r.violations]


def _parsed(**item: object) -> ParsePastedPlanOutput:
    base = {
        "index": 0,
        "place_name": "Muzeum",
        "quote": "Muzeum 10:00",
        "start_time": "10:00",
        "end_time": "11:00",
        "day": 1,
    }
    return ParsePastedPlanOutput.model_validate(
        {
            "items": [base | item],
            "matches": [
                {
                    "item_index": 0,
                    "status": "needs_confirmation",
                    "place_id": str(place_id("muzeum_miejskie")),
                    "candidates": [
                        {
                            "place_id": str(place_id("muzeum_miejskie")),
                            "name": "M",
                            "score": 0.6,
                        },
                        {"place_id": str(place_id("park")), "name": "P", "score": 0.5},
                    ],
                }
            ],
        }
    )


def _paste(parsed: ParsePastedPlanOutput, picks: dict[int, uuid.UUID]):  # ruff: ignore[missing-return-type-private-function]
    return paste_to_lint(
        parsed,
        picks,
        first_day=DAY_1,
        day_start=time(9),
        group_size=4,
        known_places=KNOWN,
    )


def test_an_unsure_match_counts_as_unknown_until_the_host_picks() -> None:
    plan, items = _paste(_parsed(), {})
    assert items[0].status == "needs_confirmation"
    assert items[0].place_id is None
    assert items[0].suggested_place_id == place_id("muzeum_miejskie")
    assert plan.days[0].items[0].place_id is None

    picked = {0: place_id("park")}
    plan, items = _paste(_parsed(), picked)
    assert items[0].status == "matched"
    assert items[0].chosen_by_host
    assert plan.days[0].items[0].place_id == place_id("park")


def test_a_confident_match_outside_the_catalog_is_not_trusted() -> None:
    parsed = _parsed()
    parsed.matches[0] = parsed.matches[0].model_copy(
        update={"status": "matched", "place_id": str(uuid.uuid4())}
    )
    plan, items = _paste(parsed, {})
    assert items[0].place_id is None
    assert plan.days[0].items[0].place_id is None


def test_amounts_are_per_person_in_minor_units_and_count_for_the_group() -> None:
    plan, _ = _paste(_parsed(amount_minor=2550, currency="PLN"), {})
    assert plan.days[0].items[0].cost == Decimal("102.00")


def test_missing_times_and_days_are_not_invented() -> None:
    parsed = ParsePastedPlanOutput.model_validate(
        {
            "items": [
                {"index": 0, "place_name": "A", "quote": "A", "end_time": "09:30"},
                {"index": 1, "place_name": "B", "quote": "B", "day": 2},
                {
                    "index": 2,
                    "place_name": "C",
                    "quote": "C",
                    "day": 2,
                    "start_time": "9:00",
                    "end_time": "8:00",
                },
            ]
        }
    )
    plan, items = _paste(parsed, {})
    assert [d.day for d in plan.days] == [DAY_1, date(2026, 10, 10)]
    first = plan.days[0].items[0]
    assert (first.start, first.end) == (time(9), time(9, 30))
    assert plan.days[1].items[0].start == time(9)
    # A contradictory end is dropped, the typical visit decides.
    assert plan.days[1].items[1].end is None
    assert [i.status for i in items] == ["unrecognized"] * 3


@pytest.mark.parametrize("amount", [None, 0])
def test_no_amount_costs_nothing(amount: int | None) -> None:
    plan, _ = _paste(_parsed(amount_minor=amount), {})
    assert plan.days[0].items[0].cost == 0
