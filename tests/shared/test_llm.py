"""Model catalog: lazy ids, composition, decision limits and hand-off fallback."""

import asyncio
import os
from types import SimpleNamespace
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
from tuttitrip.shared.config.settings import LlmSettings, get_settings
from tuttitrip.shared.llm.services import model_catalog
from tuttitrip.shared.llm.services.model_catalog import (
    ModelCatalog,
    ModelKey,
    build_model,
    catalog,
    model_id,
    model_links,
)

FAKE_KEYS = LlmSettings(
    gb10_api_key=SecretStr("test-gb10"),
    openrouter_api_key=SecretStr("test-openrouter"),
)
ELEVEN = ("o1", "o2", "o3", "o4", "o5", "o6", "o7", "o8", "o9", "o10", "o11")


def test_model_ids_use_the_shared_prefix() -> None:
    assert [model_id(key) for key in ModelKey] == [
        "tuttitrip:agent",
        "tuttitrip:interview",
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
    assert interview_agent.model == "tuttitrip:interview"
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
    assert escalation.model_name == "qwen3.8-27b-chat"

    laya = build_model(ModelKey.DECIDE_LAYA, FAKE_KEYS)
    assert isinstance(laya, FallbackModel)
    laya_model, laya_escalation = laya.models
    assert isinstance(laya_model, SystemOneModel)
    assert laya_model.base_url == "https://llm.gburek.app/laya/v1"
    assert laya_escalation.model_name == "qwen3.8-27b-chat"

    jev = build_model(ModelKey.DECIDE_CLOUD, FAKE_KEYS)
    assert isinstance(jev, SystemOneModel)
    assert jev.model_name == "typesafe/jev-1.13"
    assert jev.base_url == "https://openrouter.ai/api/v1"

    assert isinstance(build_model(ModelKey.OPENROUTER, FAKE_KEYS), OpenRouterModel)


@pytest.mark.parametrize("key", list(ModelKey))
def test_missing_keys_skip_links_or_raise_user_error(key: ModelKey) -> None:
    # Default settings carry no keys; the environment may have OPENROUTER_API_KEY.
    try:
        links = model_links(key, LlmSettings())
    except UserError:
        return
    assert links
    assert all(isinstance(link, OpenRouterModel) for link in links)


def test_chain_without_the_openrouter_key_is_just_qwen() -> None:
    settings = LlmSettings(gb10_api_key=SecretStr("test-gb10"))
    if "OPENROUTER_API_KEY" in os.environ:
        pytest.skip("OPENROUTER_API_KEY is set in this environment")
    agent = build_model(ModelKey.AGENT, settings)
    assert isinstance(agent, OpenAIChatModel)
    with pytest.raises(UserError):
        build_model(ModelKey.DECIDE_CLOUD, settings)


def test_catalog_builds_each_model_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        model_catalog, "get_settings", lambda: SimpleNamespace(llm=FAKE_KEYS)
    )
    fresh = ModelCatalog()
    assert fresh.get(ModelKey.OPENROUTER) is fresh.get(ModelKey.OPENROUTER)


def test_unknown_catalog_id_is_a_readable_user_error() -> None:
    with pytest.raises(UserError, match=r"tuttitrip:xxx.*tuttitrip:agent"):
        catalog.resolve(None, "tuttitrip:xxx")  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "key", [ModelKey.DECIDE, ModelKey.DECIDE_LAYA, ModelKey.DECIDE_CLOUD]
)
def test_decision_models_reject_eleven_options_before_any_request(
    key: ModelKey,
) -> None:
    class Pick(BaseModel):
        option: Literal[ELEVEN]  # ty: ignore[invalid-type-form]

    # An unroutable endpoint: reaching the network would be a ModelAPIError.
    unreachable = LlmSettings(
        gb10_api_key=SecretStr("x"),
        openrouter_api_key=SecretStr("x"),
        basal_base_url="http://127.0.0.1:9/v1",
        laya_base_url="http://127.0.0.1:9/v1",
        openrouter_base_url="http://127.0.0.1:9/v1",
    )
    model = build_model(key, unreachable)
    agent = Agent(model, output_type=Pick)
    with models.override_allow_model_requests(True), pytest.raises(UserError):  # ruff: ignore[boolean-positional-value-in-call]
        asyncio.run(agent.run("Wybierz"))


def test_unfillable_route_escalates_to_the_chat_model() -> None:
    def basal(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        name = "basal"
        raise UnfillableRoute(name, "final_result", 0.9)

    def qwen(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("odpowiada Qwen")])

    decide = build_model(ModelKey.DECIDE, FAKE_KEYS)
    assert isinstance(decide, FallbackModel)
    decide.models = [FunctionModel(basal), FunctionModel(qwen)]
    result = asyncio.run(Agent(decide).run("Zrób coś"))
    assert result.output == "odpowiada Qwen"


def test_interview_entry_defaults_to_the_agent_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TUTTITRIP_INTERVIEW__AGENT_MODEL", raising=False)
    get_settings.cache_clear()
    try:
        interview = build_model(ModelKey.INTERVIEW, FAKE_KEYS)
    finally:
        get_settings.cache_clear()
    assert isinstance(interview, FallbackModel)
    assert isinstance(interview.models[0], OpenAIChatModel)
    assert interview.models[0].model_name == "qwen3.8-27b"


def test_interview_agent_model_setting_uses_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TUTTITRIP_INTERVIEW__AGENT_MODEL", "openrouter:anthropic/claude-opus-5.5"
    )
    get_settings.cache_clear()
    try:
        interview = build_model(ModelKey.INTERVIEW, FAKE_KEYS)
    finally:
        get_settings.cache_clear()
    assert isinstance(interview, OpenRouterModel)
    assert interview.model_name == "anthropic/claude-opus-5.5"
