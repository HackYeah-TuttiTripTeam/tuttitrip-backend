"""Interview tools (#57, #58) driven by a scripted model over an in-memory trip."""

import asyncio
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from tests.shared.interview_world import World
from tuttitrip.interview.schemas import (
    FieldRef,
    InterviewState,
    KnowledgeField,
    ValueSource,
)
from tuttitrip.interview.services import decision_service, session_service
from tuttitrip.interview.services.interview_agent import interview_agent
from tuttitrip.places.schemas import DietTag
from tuttitrip.profiles.preferences.schemas import Constraints
from tuttitrip.profiles.schemas import AgeGroup

pytestmark = pytest.mark.filterwarnings(
    "ignore::pydantic_ai_harness.spend.UnpricedModelWarning"
)


def call(tool: str, /, **args: Any) -> ToolCallPart:  # ruff: ignore[any-type]
    return ToolCallPart(tool_name=tool, args=args)


def script(*steps: ToolCallPart | str) -> FunctionModel:
    """A model that makes one move per request: a tool call, or the final text."""
    moves = iter(steps)

    def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        move = next(moves)
        return ModelResponse(
            parts=[TextPart(move)] if isinstance(move, str) else [move]
        )

    return FunctionModel(respond)


def run(world: World, *steps: ToolCallPart | str) -> list[ModelMessage]:
    async def go() -> list[ModelMessage]:
        with interview_agent.override(model=script(*steps)):
            result = await interview_agent.run(
                "x", deps=world.deps(), message_history=[]
            )
        return result.all_messages()

    return asyncio.run(go())


def tool_returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World()
    w.install(monkeypatch)
    asyncio.run(w.add_host())
    return w


def people(world: World) -> dict[str, Any]:
    return {p.display_name: p for p in world.profiles.values()}


# --- #57: trip, people, budget --------------------------------------------


def test_one_sentence_fills_city_days_and_three_people(world: World) -> None:
    run(
        world,
        call("set_trip_basics", city="Gdańsk", start_date="2026-10-10", days=3),
        call("add_person", name="Zosia", age=6),
        call("add_person", name="Kuba", age=13),
        call("add_person", name="Babcia", age=72),
        "Zapisane.",
    )
    assert world.trip.destination == "Gdańsk"
    assert (world.trip.start_date, world.trip.end_date) == (
        date(2026, 10, 10),
        date(2026, 10, 12),
    )
    by_name = people(world)
    assert len(by_name) == 4  # the host and three more
    assert by_name["Zosia"].age_group is AgeGroup.CHILD
    assert by_name["Kuba"].age_group is AgeGroup.TEEN
    assert by_name["Babcia"].age_group is AgeGroup.SENIOR
    # defaults come from the age, not from the agent
    assert by_name["Babcia"].segment_km < by_name["Kuba"].segment_km
    assert by_name["Zosia"].weight == pytest.approx(1.0)


def test_every_write_is_marked_as_the_assistants_and_snapshots_the_panel(
    world: World,
) -> None:
    messages = run(
        world,
        call("set_trip_basics", city="Gdańsk", start_date="2026-10-10", days=3),
        call("add_person", name="Zosia", age=6),
        "ok",
    )
    results = tool_returns(messages)
    assert len(results) == 2
    for result in results:
        events = result.metadata
        assert isinstance(events, list)
        snapshot = events[0].snapshot
        assert snapshot["knowledge"]["trip"]["destination"] == "Gdańsk"
    knowledge = asyncio.run(
        session_service.get_knowledge(world.session, world.membership)
    )
    sources = {(s.field, s.profile_id): s.source for s in knowledge.sources}
    assert sources[KnowledgeField.DESTINATION, None] is ValueSource.ASSISTANT
    assert sources[KnowledgeField.DATES, None] is ValueSource.ASSISTANT
    zosia = people(world)["Zosia"]
    assert sources[KnowledgeField.PEOPLE, zosia.id] is ValueSource.ASSISTANT


def test_invalid_arguments_go_back_to_the_model_and_nothing_is_written(
    world: World,
) -> None:
    messages = run(
        world,
        call("set_trip_basics", city="Gdańsk", start_date="2026-10-10", days=400),
        call("add_person", name="Zosia", age=-3),
        call("set_budget", min="100", max="50", scope="week"),
        "Nie udało się.",
    )
    retries = [
        part.content
        for message in messages
        for part in message.parts
        if part.part_kind == "retry-prompt"
    ]
    assert len(retries) == 3
    assert world.writes == 1  # only the host's own profile
    assert world.trip.destination is None


def test_budget_scope_is_closed_and_a_reversed_range_is_refused(world: World) -> None:
    messages = run(
        world,
        call("set_budget", min="3000", max="2000", scope="total"),
        call("set_budget", min="2000", max="3000", scope="total", margin=10),
        "ok",
    )
    first, second = (
        part
        for message in messages
        for part in message.parts
        if part.part_kind == "retry-prompt" or isinstance(part, ToolReturnPart)
    )
    assert first.part_kind == "retry-prompt"
    assert world.trip.budget_total_min == Decimal(2000)
    assert world.trip.budget_total_max == Decimal(3000)
    assert world.trip.budget_day_min is None
    assert world.trip.currency == "PLN"
    assert world.trip.budget_flex_pct == 10
    assert isinstance(second, ToolReturnPart)


def test_budget_per_day_clears_the_total(world: World) -> None:
    run(
        world,
        call("set_budget", min="1000", max="2000", scope="total"),
        call("set_budget", min="300", max="400", scope="day"),
        "ok",
    )
    assert world.trip.budget_total_min is None
    assert (world.trip.budget_day_min, world.trip.budget_day_max) == (
        Decimal(300),
        Decimal(400),
    )


def test_update_person_uses_ids_and_unknown_ids_are_retried(world: World) -> None:
    run(world, call("add_person", name="Babcia", age=70), "ok")
    babcia = people(world)["Babcia"]
    messages = run(
        world,
        call("update_person", person_id=str(babcia.id), name="Babcia Hela", age=71),
        call("update_person", person_id=str(uuid.uuid4()), name="Nikt"),
        "ok",
    )
    assert babcia.display_name == "Babcia Hela"
    assert babcia.age == 71
    assert any(p.part_kind == "retry-prompt" for m in messages for p in m.parts)


def test_a_value_the_host_corrected_is_not_overwritten_without_asking(
    world: World,
) -> None:
    run(
        world,
        call("set_trip_basics", city="Gdańsk", start_date="2026-10-10", days=3),
        "ok",
    )
    # the host fixes the city in the panel, through the trips endpoint
    world.trip = world.trip.model_copy(update={"destination": "Sopot"})
    messages = run(
        world,
        call("set_trip_basics", city="Gdańsk", start_date="2026-10-10", days=3),
        "ok",
    )
    (result,) = tool_returns(messages)
    assert str(result.content).startswith("NOT SAVED")
    assert world.trip.destination == "Sopot"
    run(
        world,
        call(
            "set_trip_basics",
            city="Gdynia",
            start_date="2026-10-10",
            days=3,
            overwrite_host_values=True,
        ),
        "ok",
    )
    assert world.trip.destination == "Gdynia"


async def _tool_definitions(world: World) -> list[Any]:
    seen: list[Any] = []

    def respond(_m: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(info.function_tools)
        return ModelResponse(parts=[TextPart("ok")])

    with interview_agent.override(model=FunctionModel(respond)):
        await interview_agent.run("x", deps=world.deps())
    return seen


def test_no_tool_names_the_trip_in_its_arguments(world: World) -> None:
    tools = asyncio.run(_tool_definitions(world))
    assert {t.name for t in tools} >= {
        "set_trip_basics",
        "add_person",
        "update_person",
        "set_budget",
        "set_constraint",
        "set_diet",
        "classify_diet",
        "classify_constraint",
        "add_interest",
        "set_importance_points",
        "show_card",
    }
    for tool in tools:
        assert "trip_id" not in tool.parameters_json_schema["properties"]
        assert "session_id" not in tool.parameters_json_schema["properties"]


# --- #58: constraints, diet, interests, pool --------------------------------


def with_grandma(world: World) -> Any:  # ruff: ignore[any-type]
    run(world, call("add_person", name="Babcia", age=75), "ok")
    return people(world)["Babcia"]


def test_grandma_who_does_not_take_stairs_gets_the_stairs_constraint(
    world: World,
) -> None:
    babcia = with_grandma(world)
    run(
        world,
        call("set_constraint", person_id=str(babcia.id), kind="stairs"),
        "ok",
    )
    saved = world.saved[babcia.id]
    assert saved.constraints.stairs is True
    assert saved.constraints.wheelchair is False


def test_diet_interest_and_constraint_accumulate(world: World) -> None:
    babcia = with_grandma(world)
    pid = str(babcia.id)
    run(
        world,
        call("set_diet", person_id=pid, diet="vegetarian"),
        call("set_diet", person_id=pid, diet="gluten_free"),
        call("add_interest", person_id=pid, interest="museums", strength=0.8),
        call("set_constraint", person_id=pid, kind="heat"),
        call("set_diet", person_id=pid, diet="vegetarian", enabled=False),
        "ok",
    )
    saved = world.saved[babcia.id]
    assert saved.diet.tags == [DietTag.GLUTEN_FREE]
    assert saved.interests == {"museums": 0.8}
    assert saved.constraints == Constraints(heat=True)


def test_values_outside_the_closed_lists_are_rejected_before_any_write(
    world: World,
) -> None:
    babcia = with_grandma(world)
    pid = str(babcia.id)
    before = world.writes
    messages = run(
        world,
        call("set_diet", person_id=pid, diet="carnivore"),
        call("set_constraint", person_id=pid, kind="vertigo"),
        call("add_interest", person_id=pid, interest="museums", strength=3),
        call("set_importance_points", person_id=pid, domain="money", points=3),
        "no",
    )
    retries = [p for m in messages for p in m.parts if p.part_kind == "retry-prompt"]
    assert len(retries) == 4
    assert world.writes == before


def test_the_pool_keeps_its_ten_points_and_an_eleventh_is_refused(
    world: World,
) -> None:
    babcia = with_grandma(world)
    pid = str(babcia.id)
    messages = run(
        world,
        call("set_importance_points", person_id=pid, domain="food", points=11),
        call("set_importance_points", person_id=pid, domain="food", points=6),
        "ok",
    )
    assert any(p.part_kind == "retry-prompt" for m in messages for p in m.parts)
    pool = world.saved[babcia.id].importance_pool
    assert pool is not None
    assert pool.food == 6
    assert sum(pool.model_dump().values()) == 10


def test_a_decision_below_the_threshold_is_not_saved_but_asked_back(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    babcia = with_grandma(world)

    async def unsure(  # ruff: ignore[unused-async] stub of an async API
        _text: str,
    ) -> decision_service.Classification[DietTag]:
        return decision_service.Classification(DietTag.VEGAN, 0.4)

    monkeypatch.setattr(decision_service, "classify_diet", unsure)
    before = world.writes
    messages = run(
        world,
        call("classify_diet", person_id=str(babcia.id), text="nie lubi mięsa"),
        "Czy chodzi o dietę wegańską?",
    )
    (result,) = tool_returns(messages)
    assert str(result.content).startswith("NOT SAVED")
    assert "vegan" in str(result.content)
    assert world.writes == before
    assert babcia.id not in world.saved


def test_a_confident_decision_is_saved(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    babcia = with_grandma(world)

    async def sure(  # ruff: ignore[unused-async] stub of an async API
        _text: str,
    ) -> decision_service.Classification[DietTag]:
        return decision_service.Classification(DietTag.VEGETARIAN, 0.95)

    monkeypatch.setattr(decision_service, "classify_diet", sure)
    run(
        world,
        call("classify_diet", person_id=str(babcia.id), text="je tylko warzywa"),
        "ok",
    )
    assert world.saved[babcia.id].diet.tags == [DietTag.VEGETARIAN]


def test_the_preference_snapshot_is_in_the_stream_state(world: World) -> None:
    babcia = with_grandma(world)
    messages = run(
        world,
        call("set_constraint", person_id=str(babcia.id), kind="stairs"),
        "ok",
    )
    (result,) = tool_returns(messages)
    assert isinstance(result.metadata, list)
    state = InterviewState.model_validate(result.metadata[0].snapshot)
    assert state.knowledge is not None
    ref = FieldRef(field=KnowledgeField.PREFERENCES, profile_id=babcia.id)
    assert any(
        (s.field, s.profile_id) == (ref.field, ref.profile_id)
        for s in state.knowledge.sources
    )
