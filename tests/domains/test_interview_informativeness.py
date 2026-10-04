"""Question informativeness (#62): the solver's what-if, the ranking and the choice."""

import asyncio
import uuid
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from tests.domains.test_interview import _person
from tests.domains.test_interview_questions import COMPLETE, known, two
from tests.fixtures.personas import reference_family
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tests.shared.interview_world import World
from tuttitrip.interview import constants
from tuttitrip.interview.logic import informativeness, next_question
from tuttitrip.interview.schemas import (
    CardKind,
    NextQuestion,
    QuestionField,
    QuestionKey,
)
from tuttitrip.interview.services import question_service
from tuttitrip.interview.services.interview_agent import interview_agent
from tuttitrip.planning.logic import what_if
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.plans.services.plan_service import PlanInputError
from tuttitrip.planning.schemas import WhatIfField, WhatIfTarget

pytestmark = pytest.mark.filterwarnings(
    "ignore::pydantic_ai_harness.spend.UnpricedModelWarning"
)

CITY = {"city_slug": "krakow", "destination": "Kraków"}
WEDNESDAY = date(2026, 10, 7)
PEOPLE = {p.key: p.id for p in reference_family().people}


def budget() -> WhatIfTarget:
    return WhatIfTarget(field=WhatIfField.BUDGET)


def of(field: WhatIfField, who: str) -> WhatIfTarget:
    return WhatIfTarget(field=field, person_id=PEOPLE[who])


# --- the solver's what-if ------------------------------------------------------------


def test_the_field_whose_answers_change_the_plan_scores_higher() -> None:
    data = planning_input(reference(), lodging=False)
    scores = what_if.impacts(
        data,
        [budget(), of(WhatIfField.REQUIREMENTS, "ty"), of(WhatIfField.PACE, "ty")],
    )
    assert scores is not None
    # A budget that halves or grows the plan reshuffles it; stricter stairs for
    # somebody the plan already spares changes nothing.
    assert scores[budget()] >= what_if.HASH_WEIGHT / 2
    assert scores[of(WhatIfField.REQUIREMENTS, "ty")] < 0.1
    assert scores[budget()] > scores[of(WhatIfField.REQUIREMENTS, "ty")]
    assert scores[budget()] > scores[of(WhatIfField.PACE, "ty")]


def test_the_same_data_gives_the_same_scores() -> None:
    data = planning_input(reference(), lodging=False)
    targets = [budget(), of(WhatIfField.PACE, "babcia")]
    assert what_if.impacts(data, targets) == what_if.impacts(data, targets)


def test_running_out_of_time_gives_no_scores() -> None:
    data = planning_input(reference(), lodging=False)
    assert what_if.impacts(data, [budget()], budget_seconds=0.0) is None


def test_every_question_has_answers_to_try() -> None:
    data = planning_input(reference(), lodging=False)
    base = what_if.solve(data, max_evaluations=what_if.PROBE_EVALUATIONS)
    babcia = PEOPLE["babcia"]
    for field in WhatIfField:
        target = WhatIfTarget(
            field=field,
            person_id=None
            if field in {WhatIfField.DATES, WhatIfField.PEOPLE, WhatIfField.BUDGET}
            else babcia,
        )
        assert what_if.answers(data, base, target), field
    # A person who is not in the input, or a plan with no cost, has nothing to try.
    assert what_if.answers(data, base, of(WhatIfField.PACE, "babcia")) != []
    ghost = WhatIfTarget(field=WhatIfField.PACE, person_id=uuid.uuid4())
    assert what_if.answers(data, base, ghost) == []


def test_the_people_answers_add_somebody_without_touching_the_group() -> None:
    data = planning_input(reference(), lodging=False)
    base = what_if.solve(data, max_evaluations=what_if.PROBE_EVALUATIONS)
    adult, child = what_if.answers(data, base, WhatIfTarget(field=WhatIfField.PEOPLE))
    assert len(adult.people) == len(child.people) == len(data.people) + 1
    assert adult.people[-1].id != child.people[-1].id
    assert child.people[-1].age == what_if.CHILD_AGE
    assert data.people == adult.people[:-1] == child.people[:-1]


# --- ranking --------------------------------------------------------------------------


def questions(*fields: QuestionField) -> list[NextQuestion]:
    return [
        NextQuestion(
            field=f, card_kind=constants.CARD_OF_FIELD[f], person_id=None, options=[]
        )
        for f in fields
    ]


def test_the_highest_score_wins_and_carries_its_impact() -> None:
    candidates = questions(QuestionField.BUDGET, QuestionField.PEOPLE)
    best = informativeness.pick(
        candidates, {budget(): 0.4, WhatIfTarget(field=WhatIfField.PEOPLE): 1.7}
    )
    assert best is not None
    assert (best.field, best.impact) == (QuestionField.PEOPLE, 1.7)


def test_ties_go_to_the_field_name_and_unmeasured_questions_come_last() -> None:
    candidates = questions(
        QuestionField.DIET, QuestionField.PEOPLE, QuestionField.BUDGET
    )
    tied = {budget(): 0.5, WhatIfTarget(field=WhatIfField.PEOPLE): 0.5}
    best = informativeness.pick(candidates, tied)
    assert best is not None
    assert best.field is QuestionField.BUDGET  # "budget" < "people"
    nothing = informativeness.pick(questions(QuestionField.DIET), {})
    assert nothing is not None
    assert (nothing.field, nothing.impact) == (QuestionField.DIET, 0.0)
    assert informativeness.pick([], {}) is None


def test_the_ranking_does_not_depend_on_the_order_of_the_candidates() -> None:
    fields = [QuestionField.BUDGET, QuestionField.PEOPLE, QuestionField.DATES]
    scores = {budget(): 1.0, WhatIfTarget(field=WhatIfField.PEOPLE): 1.0}
    first = informativeness.pick(questions(*fields), scores)
    second = informativeness.pick(questions(*reversed(fields)), scores)
    assert first == second


def test_the_card_is_the_fixed_map_without_any_model() -> None:
    for field in QuestionField:
        assert informativeness.card_kind(field) is constants.CARD_OF_FIELD[field]
    assert informativeness.card_kind(QuestionField.PACE) is CardKind.SLIDER
    best = informativeness.pick(questions(QuestionField.BUDGET), {budget(): 1.0})
    assert best is not None
    assert best.card_kind is CardKind.BUDGET_RANGE


def test_open_questions_list_one_per_field_in_the_fixed_order() -> None:
    panel = known(two(), {"city_slug": "krakow", "destination": "Kraków"})
    opened = next_question.open_questions(panel)
    fields = [q.field for q in opened]
    assert fields == list(dict.fromkeys(fields))
    assert fields[:3] == [
        QuestionField.DATES,
        QuestionField.BUDGET,
        QuestionField.PACE,
    ]
    assert next_question.next_question(panel) == opened[0]
    asked = {QuestionKey(field=QuestionField.DATES)}
    assert next_question.open_questions(panel, asked)[0].field is QuestionField.DATES


# --- the choice in the interview ------------------------------------------------------


@pytest.fixture
def measure(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(plan_service, "measure_impacts", mock)
    return mock


def choose(panel: Any, asked: set[QuestionKey] | None = None) -> NextQuestion | None:  # ruff: ignore[any-type]
    return asyncio.run(
        question_service.choose(
            AsyncMock(), _membership(), panel, asked or set(), WEDNESDAY
        )
    )


def _membership() -> Any:  # ruff: ignore[any-type]
    return World().membership


def test_the_measured_best_question_is_asked_first(measure: AsyncMock) -> None:
    panel = known([_person("Host")], CITY)
    measure.side_effect = lambda *_a: {
        t: (2.0 if t.field is WhatIfField.PEOPLE else 0.1) for t in _a[3]
    }
    chosen = choose(panel)
    assert chosen is not None
    assert (chosen.field, chosen.impact) == (QuestionField.PEOPLE, 2.0)
    assert measure.await_args is not None
    assumptions = measure.await_args.args[2]
    assert assumptions.min_people == 2


def test_without_a_measurement_the_fixed_order_decides(measure: AsyncMock) -> None:
    panel = known([_person("Host")], CITY)
    measure.return_value = None  # out of time
    chosen = choose(panel)
    assert chosen == next_question.next_question(panel)
    assert chosen is not None
    assert chosen.impact is None


def test_unplannable_data_falls_back_to_the_fixed_order(measure: AsyncMock) -> None:
    panel = known([_person("Host")], CITY)
    measure.side_effect = PlanInputError("Unknown city 'x'")
    assert choose(panel) == next_question.next_question(panel)


def test_the_destination_comes_first_and_nothing_is_measured(
    measure: AsyncMock,
) -> None:
    chosen = choose(known([_person("Host")]))
    assert chosen is not None
    assert chosen.field is QuestionField.DESTINATION
    measure.assert_not_awaited()


def test_nothing_left_to_ask_is_none(measure: AsyncMock) -> None:
    panel = known(two(), {**COMPLETE, **CITY}, filled_prefs=True)
    asked = {
        QuestionKey(field=f, person_id=p.id)
        for f in constants.PERSON_FIELDS
        for p in panel.people
    }
    assert choose(panel, asked) is None
    measure.assert_not_awaited()


def test_the_solver_runs_once_per_state_of_the_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    world.install(monkeypatch)
    asyncio.run(world.add_host())
    world.trip = world.trip.model_copy(update=CITY)
    chosen = AsyncMock(return_value=None)
    monkeypatch.setattr(question_service, "choose", chosen)
    requests = 3

    def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("ok")])

    deps = world.deps()

    async def go() -> None:
        with interview_agent.override(model=FunctionModel(respond)):
            for _ in range(requests):
                await interview_agent.run("hej", deps=deps)

    asyncio.run(go())
    assert chosen.await_count == 1  # the panel did not change between requests


def test_the_instructions_tell_the_model_the_impact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    world.install(monkeypatch)
    asyncio.run(world.add_host())
    world.trip = world.trip.model_copy(update=CITY)
    best = NextQuestion(
        field=QuestionField.BUDGET,
        card_kind=CardKind.BUDGET_RANGE,
        options=[],
        impact=1.25,
    )
    monkeypatch.setattr(question_service, "choose", AsyncMock(return_value=best))
    seen: list[str] = []

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(str(info.instructions))
        return ModelResponse(parts=[TextPart("ok")])

    async def go() -> None:
        with interview_agent.override(model=FunctionModel(respond)):
            await interview_agent.run("hej", deps=world.deps())

    asyncio.run(go())
    assert '"impact":1.25' in seen[0]
    assert '"field":"budget"' in seen[0]


# --- the planning service ------------------------------------------------------------


def test_the_service_caches_a_measurement_of_the_same_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = planning_input(reference(), lodging=False)
    gather = AsyncMock(return_value=(data, {}, 1.0))
    monkeypatch.setattr(plan_service, "_gather", gather)
    solved = MagicMock()
    plan_service.impact_cache.clear()
    targets = (budget(),)
    session = AsyncMock()
    membership = World().membership

    def impacts(*_args: object, **_kw: object) -> dict[WhatIfTarget, float]:
        solved()
        return {budget(): 1.0}

    monkeypatch.setattr(what_if, "impacts", impacts)

    async def twice() -> list[Any]:
        return [
            await plan_service.measure_impacts(session, membership, None, targets, 3.0)
            for _ in range(2)
        ]

    first, second = asyncio.run(twice())
    assert first == second == {budget(): 1.0}
    assert solved.call_count == 1


def test_the_service_does_not_cache_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = planning_input(reference(), lodging=False)
    monkeypatch.setattr(
        plan_service, "_gather", AsyncMock(return_value=(data, {}, 1.0))
    )
    calls: list[int] = []

    def late(*_args: object, **_kw: object) -> None:
        calls.append(1)

    monkeypatch.setattr(what_if, "impacts", late)
    plan_service.impact_cache.clear()
    membership = World().membership

    async def twice() -> None:
        for _ in range(2):
            assert (
                await plan_service.measure_impacts(
                    AsyncMock(), membership, None, (budget(),), 3.0
                )
                is None
            )

    asyncio.run(twice())
    assert len(calls) == 2
    assert plan_service.impact_cache == {}
