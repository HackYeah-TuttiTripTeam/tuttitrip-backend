"""Question order (#59), the importance pool and the calendar: pure logic."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from tests.domains.test_interview import _person, _prefs, _trip
from tuttitrip.interview import constants
from tuttitrip.interview.logic import calendar, importance, knowledge, next_question
from tuttitrip.interview.schemas import (
    CardKind,
    ConstraintKind,
    FieldRef,
    KnowledgeField,
    KnowledgeRead,
    QuestionField,
    QuestionKey,
)
from tuttitrip.profiles.preferences.schemas import POOL_TOTAL, Constraints
from tuttitrip.profiles.schemas import ProfileRead

DAY = date(2026, 11, 1)


def known(
    people: list[ProfileRead],
    trip: dict[str, Any] | None = None,
    *,
    filled_prefs: bool = False,
    host_prefs: set[uuid.UUID] | None = None,
) -> KnowledgeRead:
    """The panel for a trip, built by the same code the endpoint uses."""
    full = _trip(**(trip or {}))
    prefs = [_prefs(p, filled=filled_prefs) for p in people]
    current = knowledge.values(full, people, prefs)
    by_assistant = {
        ref: v.digest
        for ref, v in current.items()
        if not (host_prefs and ref.profile_id in host_prefs)
    }
    return KnowledgeRead(
        trip=full,
        people=people,
        preferences=prefs,
        missing=knowledge.missing(current),
        sources=knowledge.sources(current, by_assistant),
    )


COMPLETE = {
    "destination": "Kraków",
    "start_date": DAY,
    "end_date": DAY,
    "currency": "PLN",
    "budget_total_min": Decimal(1000),
    "budget_total_max": Decimal(2000),
}


def two() -> list[ProfileRead]:
    return [_person("Ana"), _person("Bob")]


def test_an_empty_trip_starts_with_the_destination() -> None:
    question = next_question.next_question(known([_person("Host")]))
    assert question is not None
    assert (question.field, question.card_kind) == (
        QuestionField.DESTINATION,
        CardKind.CHOICE,
    )
    assert question.person_id is None


def test_the_order_follows_the_table_and_never_asks_a_filled_field() -> None:
    people = [_person("Host")]
    steps = [
        ({}, QuestionField.DESTINATION),
        ({"destination": "Kraków"}, QuestionField.DATES),
        (
            {"destination": "Kraków", "start_date": DAY, "end_date": DAY},
            QuestionField.PEOPLE,
        ),
    ]
    for trip, expected in steps:
        question = next_question.next_question(known(people, trip))
        assert question is not None
        assert question.field is expected
    complete_people = {**COMPLETE}
    del complete_people["budget_total_min"], complete_people["budget_total_max"]
    no_budget = next_question.next_question(known(two(), complete_people))
    assert no_budget is not None
    assert (no_budget.field, no_budget.card_kind) == (
        QuestionField.BUDGET,
        CardKind.BUDGET_RANGE,
    )
    after = next_question.next_question(known(two(), COMPLETE))
    assert after is not None
    assert after.field is QuestionField.PACE


def test_pace_is_asked_about_the_slowest_person() -> None:
    fast = _person("Fast").model_copy(update={"segment_km": 5, "daily_km": 12})
    slow = _person("Slow").model_copy(update={"segment_km": 1, "daily_km": 3})
    question = next_question.next_question(known([fast, slow], COMPLETE))
    assert question is not None
    assert (question.field, question.card_kind) == (QuestionField.PACE, CardKind.SLIDER)
    assert question.person_id == slow.id


def test_person_questions_walk_the_table_once_each() -> None:
    fast = _person("Fast").model_copy(update={"segment_km": 5})
    slow = _person("Slow").model_copy(update={"segment_km": 1})
    panel = known([fast, slow], COMPLETE)
    asked: set[QuestionKey] = set()
    order: list[tuple[QuestionField, uuid.UUID | None]] = []
    while question := next_question.next_question(panel, asked):
        order.append((question.field, question.person_id))
        asked.add(QuestionKey(field=question.field, person_id=question.person_id))
    assert order == [
        (QuestionField.PACE, slow.id),
        (QuestionField.IMPORTANCE, slow.id),
        (QuestionField.IMPORTANCE, fast.id),
        (QuestionField.REQUIREMENTS, slow.id),
        (QuestionField.INTERESTS, slow.id),
        (QuestionField.INTERESTS, fast.id),
        (QuestionField.DIET, slow.id),
        (QuestionField.DIET, fast.id),
    ]
    # nothing left: the assistant offers to build the plan
    assert next_question.next_question(panel, asked) is None


def test_each_card_kind_of_the_table_is_one_of_the_eight() -> None:
    assert len(CardKind) == 8
    assert set(constants.CARD_OF_FIELD.values()) <= set(CardKind)
    assert set(constants.CARD_OF_FIELD) == set(QuestionField)


def test_a_person_whose_preferences_the_host_filled_is_not_asked() -> None:
    ana, bob = two()
    panel = known([ana, bob], COMPLETE, filled_prefs=True, host_prefs={ana.id})
    asked: set[QuestionKey] = set()
    people_asked: set[uuid.UUID | None] = set()
    while question := next_question.next_question(panel, asked):
        people_asked.add(question.person_id)
        asked.add(QuestionKey(field=question.field, person_id=question.person_id))
    assert people_asked == {bob.id}


def test_a_trip_field_the_host_set_counts_as_filled() -> None:
    panel = known(two(), COMPLETE)
    destination = FieldRef(field=KnowledgeField.DESTINATION)
    assert destination not in panel.missing
    question = next_question.next_question(panel)
    assert question is not None
    assert question.field is not QuestionField.DESTINATION


def test_the_same_knowledge_gives_the_same_question() -> None:
    panel = known(two(), COMPLETE)
    assert next_question.next_question(panel) == next_question.next_question(panel)


def test_options_come_from_the_catalog_lists_and_fit_the_decision_limit() -> None:
    panel = known(two(), COMPLETE)
    asked: set[QuestionKey] = set()
    options: dict[QuestionField, list[str]] = {}
    while question := next_question.next_question(panel, asked):
        options.setdefault(question.field, question.options)
        asked.add(QuestionKey(field=question.field, person_id=question.person_id))
    assert options[QuestionField.REQUIREMENTS] == [k.value for k in ConstraintKind]
    assert "vegetarian" in options[QuestionField.DIET]
    assert "museums" in options[QuestionField.INTERESTS]
    assert len(options[QuestionField.IMPORTANCE]) == 5
    assert len(options[QuestionField.DIET]) <= 10  # basal's pick-one limit


def test_constraint_kinds_are_exactly_the_yes_no_flags() -> None:
    flags = {
        name
        for name, field in Constraints.model_fields.items()
        if field.annotation is bool
    }
    assert {k.value for k in ConstraintKind} == flags


# --- importance pool -------------------------------------------------------

POOL = {"lodging": 2, "food": 2, "attractions": 3, "pace": 1, "cost": 2}


@pytest.mark.parametrize("points", range(POOL_TOTAL + 1))
def test_the_pool_always_adds_up_to_ten(points: int) -> None:
    moved = importance.set_points(POOL, "food", points)
    assert moved["food"] == points
    assert sum(moved.values()) == POOL_TOTAL
    assert all(v >= 0 for v in moved.values())


def test_the_others_keep_their_proportions() -> None:
    moved = importance.set_points(POOL, "food", 4)
    # attractions had the most, so it keeps the most
    assert moved == {
        "lodging": 2,
        "food": 4,
        "attractions": 2,
        "pace": 0,
        "cost": 2,
    } or (moved["attractions"] >= moved["lodging"])
    assert importance.set_points(POOL, "food", POOL_TOTAL) == {
        "lodging": 0,
        "food": 10,
        "attractions": 0,
        "pace": 0,
        "cost": 0,
    }


def test_points_outside_the_pool_and_unknown_domains_are_refused() -> None:
    with pytest.raises(ValueError, match="0 to 10"):
        importance.set_points(POOL, "food", 11)
    with pytest.raises(ValueError, match="0 to 10"):
        importance.set_points(POOL, "food", -1)
    with pytest.raises(ValueError, match="Unknown domain"):
        importance.set_points(POOL, "money", 3)


def test_an_all_zero_remainder_is_spread_evenly() -> None:
    only_food = {"lodging": 0, "food": 10, "attractions": 0, "pace": 0, "cost": 0}
    moved = importance.set_points(only_food, "food", 6)
    assert sum(moved.values()) == POOL_TOTAL
    assert moved["food"] == 6
    assert sorted(v for k, v in moved.items() if k != "food") == [1, 1, 1, 1]


# --- calendar ---------------------------------------------------------------


def test_the_calendar_names_the_weekdays_in_polish() -> None:
    days = calendar.upcoming_days(date(2026, 10, 4), 7)
    assert days[0] == "niedziela 2026-10-04"
    assert days[6] == "sobota 2026-10-10"
    assert len(calendar.upcoming_days(date(2026, 10, 4))) == constants.CALENDAR_DAYS
