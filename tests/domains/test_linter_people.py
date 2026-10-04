"""Linter rules about people: distance, pace, rest window, accessibility."""

from collections.abc import Callable
from datetime import date, time
from uuid import UUID

import pytest

from tests.domains.test_schedule import place
from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.linter.logic import accessibility, distance, pace, rest_window
from tuttitrip.planning.linter.logic.rules import lint
from tuttitrip.planning.linter.schemas import (
    Finding,
    LintContext,
    LintDay,
    LintItem,
    LintLunch,
    LintPerson,
    LintPlan,
    Severity,
)
from tuttitrip.planning.logic.schedule import daily_distance_km

DAY = date(2026, 10, 12)
Check = Callable[[LintPlan, LintContext], list[Finding]]


def person(n: int = 1, **fields: object) -> LintPerson:
    base: dict[str, object] = {
        "id": UUID(int=n),
        "name": f"Osoba {n}",
        "segment_km": 2.0,
        "daily_km": 8.0,
    }
    return LintPerson.model_validate(base | fields)


def stop(p: PlaceRead, start: str, end: str | None = None) -> LintItem:
    return LintItem(
        name=p.name,
        place_id=p.id,
        start=time.fromisoformat(start),
        end=None if end is None else time.fromisoformat(end),
    )


def run(
    rule: Check, items: list[LintItem], places: list[PlaceRead], **ctx: object
) -> list[Finding]:
    plan = LintPlan(days=[LintDay(day=DAY, items=items)])
    context = LintContext.model_validate(
        {"places": places, "timezone": "Europe/Warsaw", "budget": 0} | ctx
    )
    return rule(plan, context)


def kinds(findings: list[Finding]) -> list[Severity]:
    return [f.severity for f in findings]


def test_distance_violation_names_person_and_stop() -> None:
    child = person(1, name="Zosia", segment_km=1.0)
    far = place(1, km=2.0)
    found = run(distance.check, [stop(far, "10:00", "11:00")], [far], people=[child])
    assert kinds(found) == [Severity.VIOLATION]
    assert found[0].person_id == child.id
    assert found[0].person_name == "Zosia"
    assert (found[0].day, found[0].position) == (DAY, 0)


def test_distance_between_s_and_one_and_a_half_s_is_warning() -> None:
    far = place(1, km=1.3)
    found = run(
        distance.check,
        [stop(far, "10:00", "11:00")],
        [far],
        people=[person(segment_km=1.0)],
    )
    assert kinds(found) == [Severity.WARNING]


@pytest.mark.parametrize("km", [0.5, 1.0])
def test_distance_within_s_is_clean(km: float) -> None:
    near = place(1, km=km)
    assert not run(
        distance.check,
        [stop(near, "10:00", "11:00")],
        [near],
        people=[person(segment_km=1.0)],
    )


def test_distance_boundary_is_not_a_violation() -> None:
    edge = place(1, km=1.5)
    found = run(
        distance.check,
        [stop(edge, "10:00", "11:00")],
        [edge],
        people=[person(segment_km=1.0)],
    )
    assert kinds(found) == [Severity.WARNING]


def test_pace_points_at_the_stop_that_crosses_the_limit() -> None:
    walker = person(daily_km=4.0)
    places = [place(n, km=2.0) for n in (1, 2, 3, 4)]
    items = [
        stop(p, f"{9 + i * 2:02d}:00", f"{10 + i * 2:02d}:00")
        for i, p in enumerate(places)
    ]
    found = run(pace.check, items, places, people=[walker])
    # L_d = 8 km > 1.5 * 4 km = 6 km; the running total passes 6 at the 4th stop.
    assert kinds(found) == [Severity.VIOLATION]
    assert found[0].position == 3
    assert found[0].person_id == walker.id


def test_pace_between_d_and_one_and_a_half_d_is_warning() -> None:
    places = [place(n, km=2.0) for n in (1, 2, 3)]
    items = [
        stop(p, f"{9 + i * 2:02d}:00", f"{10 + i * 2:02d}:00")
        for i, p in enumerate(places)
    ]
    found = run(pace.check, items, places, people=[person(daily_km=5.0)])
    assert kinds(found) == [Severity.WARNING]
    assert found[0].position == 2


def test_pace_uses_the_distance_of_the_schedule() -> None:
    places = [place(n, km=0.1) for n in range(1, 8)]
    items = [stop(p, f"{9 + i}:00".zfill(5), None) for i, p in enumerate(places)]
    total = daily_distance_km(p.segment_km for p in places)
    found = run(pace.check, items, places, people=[person(daily_km=total)])
    assert found == []


def test_pace_counts_each_day_alone() -> None:
    p = place(1, km=3.0)
    plan = LintPlan(
        days=[
            LintDay(day=DAY, items=[stop(p, "10:00", "11:00")]),
            LintDay(day=date(2026, 10, 13), items=[stop(p, "10:00", "11:00")]),
        ]
    )
    context = LintContext(
        places=[p], timezone="Europe/Warsaw", budget=0, people=[person(daily_km=3.0)]
    )
    assert pace.check(plan, context) == []


def test_nap_overlap_is_a_violation() -> None:
    napper = person(nap_start=time(13), nap_minutes=60)
    visit = place(1, visit=90)
    found = run(
        rest_window.check, [stop(visit, "12:30", "14:00")], [visit], people=[napper]
    )
    assert kinds(found) == [Severity.VIOLATION]
    assert found[0].person_id == napper.id


@pytest.mark.parametrize(("start", "end"), [("11:00", "13:00"), ("14:00", "15:00")])
def test_visit_next_to_the_nap_is_fine(start: str, end: str) -> None:
    visit = place(1)
    assert not run(
        rest_window.check,
        [stop(visit, start, end)],
        [visit],
        people=[person(nap_start=time(13), nap_minutes=60)],
    )


def test_nap_uses_the_typical_visit_when_end_is_missing() -> None:
    visit = place(1, visit=60)
    found = run(
        rest_window.check,
        [stop(visit, "12:30")],
        [visit],
        people=[person(nap_start=time(13), nap_minutes=60)],
    )
    assert kinds(found) == [Severity.VIOLATION]


def test_lunch_needs_a_free_gap_in_its_window() -> None:
    lunch = LintLunch(earliest=time(12), latest=time(14), minutes=45)
    long_visit, evening = place(1), place(2)
    packed = [stop(long_visit, "10:00", "15:00"), stop(evening, "15:00", "16:00")]
    found = run(rest_window.check, packed, [long_visit, evening], lunch=lunch)
    assert kinds(found) == [Severity.VIOLATION]
    assert found[0].person_id is None
    gap = [stop(long_visit, "10:00", "13:00"), stop(evening, "14:00", "16:00")]
    assert not run(rest_window.check, gap, [long_visit, evening], lunch=lunch)


def test_lunch_gap_starting_after_a_visit_counts() -> None:
    lunch = LintLunch(earliest=time(12), latest=time(14), minutes=45)
    a, b = place(1), place(2)
    items = [stop(a, "11:00", "13:30"), stop(b, "14:30", "16:00")]
    assert not run(rest_window.check, items, [a, b], lunch=lunch)


def test_wheelchair_with_stairs_is_a_violation() -> None:
    rider = person(wheelchair=True)
    castle = place(1).model_copy(update={"stairs": 0.6})
    found = run(
        accessibility.check, [stop(castle, "10:00", "11:00")], [castle], people=[rider]
    )
    assert kinds(found) == [Severity.VIOLATION]


def test_wheelchair_no_is_a_violation_unknown_is_a_warning() -> None:
    rider = person(wheelchair=True)
    no = place(1).model_copy(update={"wheelchair": False})
    unknown = place(2)
    yes = place(3).model_copy(update={"wheelchair": True, "stairs": 0.5})
    items = [
        stop(no, "09:00", "10:00"),
        stop(unknown, "11:00", "12:00"),
        stop(yes, "13:00", "14:00"),
    ]
    found = run(accessibility.check, items, [no, unknown, yes], people=[rider])
    assert [(f.position, f.severity) for f in found] == [
        (0, Severity.VIOLATION),
        (1, Severity.WARNING),
    ]


def test_stairs_sensitivity_reaches_the_e0_limit() -> None:
    sensitive = person(stairs_sensitivity=1.0)
    steps = place(1).model_copy(update={"stairs": 0.9})
    mild = place(2).model_copy(update={"stairs": 0.8})
    items = [stop(steps, "09:00", "10:00"), stop(mild, "11:00", "12:00")]
    found = run(accessibility.check, items, [steps, mild], people=[sensitive])
    assert [f.position for f in found] == [0]


def test_person_without_limits_is_never_flagged_on_access() -> None:
    steps = place(1).model_copy(update={"stairs": 1.0, "wheelchair": False})
    assert not run(
        accessibility.check, [stop(steps, "09:00", "10:00")], [steps], people=[person()]
    )


def test_lint_reports_all_four_rules_and_scores_violations() -> None:
    far = place(1, km=3.0)
    plan = LintPlan(days=[LintDay(day=DAY, items=[stop(far, "10:00", "11:00")])])
    context = LintContext(
        places=[far],
        timezone="Europe/Warsaw",
        budget=0,
        people=[person(segment_km=1.0)],
    )
    report = lint(plan, context)
    assert [r.rule for r in report.results][-5:-1] == [
        "distance",
        "pace",
        "rest_window",
        "accessibility",
    ]
    assert report.count == 1
    assert report.score == 3
    assert lint(plan, context).digest == report.digest
