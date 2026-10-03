"""Model catalog: lazy ids, composition, decision limits and hand-off fallback."""

import asyncio
from typing import Literal

import pytest
from pydantic import BaseModel, SecretStr
from pydantic_ai import Agent, models
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.decision import UnfillableRoute
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.models.system_one import SystemOneModel
from pydantic_ai.models.test import TestModel

from tuttitrip.interview.services.interview_agent import interview_agent
from tuttitrip.planning.services.planner_agent import planner_agent
from tuttitrip.shared.config.settings import LlmSettings
from tuttitrip.shared.llm.services.model_catalog import (
    ModelCatalog,
    ModelKey,
    build_model,
    catalog,
    model_id,
)

FAKE_KEYS = LlmSettings(
    gb10_api_key=SecretStr("test-gb10"),
    openrouter_api_key=SecretStr("test-openrouter"),
)
ELEVEN = ("o1", "o2", "o3", "o4", "o5", "o6", "o7", "o8", "o9", "o10", "o11")


def test_model_ids_use_the_shared_prefix() -> None:
    assert [model_id(key) for key in ModelKey] == [
        "tuttitrip:agent",
        "tuttitrip:chat",
        "tuttitrip:decide",
        "tuttitrip:decide-laya",
        "tuttitrip:decide-cloud",
        "tuttitrip:openrouter",
    ]


def test_agents_import_and_run_without_any_provider_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "TUTTITRIP_LLM__GB10_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    fresh = ModelCatalog()
    with fresh.override(TestModel()):
        assert fresh.get(ModelKey.DECIDE) is fresh.get(ModelKey.AGENT)
    assert interview_agent.model == "tuttitrip:agent"
    assert planner_agent.model == "tuttitrip:agent"
    assert catalog.resolve(None, "openai:gpt-5.2") is None  # ty: ignore[invalid-argument-type]


def test_composition_of_each_entry() -> None:
    agent = build_model(ModelKey.AGENT, FAKE_KEYS)
    assert isinstance(agent, FallbackModel)
    first, second = agent.models
    assert isinstance(first, OpenAIChatModel)
    assert first.model_name == "qwen3.8-27b"
    assert isinstance(second, OpenRouterModel)

    chat = build_model(ModelKey.CHAT, FAKE_KEYS)
    assert isinstance(chat, FallbackModel)
    assert chat.models[0].model_name == "qwen3.8-27b-chat"

    decide = build_model(ModelKey.DECIDE, FAKE_KEYS)
    assert isinstance(decide, FallbackModel)
    basal, escalation = decide.models
    assert isinstance(basal, SystemOneModel)
    assert basal.model_name == "basal"
    assert basal.base_url == "https://llm.gburek.app/basal/v1"
    assert isinstance(escalation, OpenAIChatModel)
    assert escalation.model_name == "qwen3.8-27b"

    laya = build_model(ModelKey.DECIDE_LAYA, FAKE_KEYS)
    assert isinstance(laya, SystemOneModel)
    assert laya.base_url == "https://llm.gburek.app/laya/v1"

    jev = build_model(ModelKey.DECIDE_CLOUD, FAKE_KEYS)
    assert isinstance(jev, SystemOneModel)
    assert jev.model_name == "typesafe/jev-1.13"
    assert jev.base_url == "https://openrouter.ai/api/v1"

    assert isinstance(build_model(ModelKey.OPENROUTER, FAKE_KEYS), OpenRouterModel)


def test_catalog_builds_each_model_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    fresh = ModelCatalog()
    assert fresh.get(ModelKey.OPENROUTER) is fresh.get(ModelKey.OPENROUTER)


def test_decide_rejects_eleven_options_before_any_request() -> None:
    class Pick(BaseModel):
        option: Literal[ELEVEN]  # ty: ignore[invalid-type-form]

    agent = Agent(
        model_id(ModelKey.DECIDE),
        output_type=Pick,
        capabilities=[catalog.capability()],
        defer_model_check=True,
    )
    # An unroutable endpoint: reaching the network would be a ModelAPIError.
    unreachable = LlmSettings(
        basal_base_url="http://127.0.0.1:9/v1", gb10_api_key=SecretStr("x")
    )
    model = build_model(ModelKey.DECIDE, unreachable)
    with models.override_allow_model_requests(True), pytest.raises(UserError):  # ruff: ignore[boolean-positional-value-in-call]
        asyncio.run(agent.run("Wybierz", model=model))


def test_unfillable_route_escalates_to_the_language_model() -> None:
    def basal(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        name = "basal"
        raise UnfillableRoute(name, "final_result", 0.9)

    def qwen(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("odpowiada Qwen")])

    escalating = FallbackModel(FunctionModel(basal), FunctionModel(qwen))
    result = asyncio.run(Agent(escalating).run("Zrób coś"))
    assert result.output == "odpowiada Qwen"
