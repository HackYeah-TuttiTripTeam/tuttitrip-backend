"""Model catalog for Pydantic AI agents (R1: the only way to models).

Agents pass text ids such as ``tuttitrip:agent`` and the catalog's
``ResolveModelId`` capability turns them into models built from settings,
lazily, so the app and the tests run without keys and no real provider is built
until a model is actually used. This mirrors the catalog of tuttitrip-worker
(same ids). Tests swap every id for a ``TestModel``/``FunctionModel`` with
:meth:`ModelCatalog.override`.

| Id | Model |
| --- | --- |
| ``tuttitrip:agent`` | Qwen ``agent_model`` (thinking, tools), OpenRouter fallback |
| ``tuttitrip:chat`` | Qwen ``chat_model``, OpenRouter fallback |
| ``tuttitrip:decide`` | basal, escalates to the Qwen agent model |
| ``tuttitrip:decide-laya`` | Laya |
| ``tuttitrip:decide-cloud`` | JEV on OpenRouter |
| ``tuttitrip:openrouter`` | OpenRouter chat model |
"""

import os
from collections.abc import Callable, Generator
from contextlib import contextmanager
from enum import StrEnum
from typing import Any, Final

from pydantic_ai.capabilities import ResolveModelId
from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.models.system_one import SystemOneModel
from pydantic_ai.profiles.decision import DecisionModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.providers.system_one import SystemOneProvider

from tuttitrip.shared.config.settings import LlmSettings, get_settings

MODEL_ID_PREFIX: Final = "tuttitrip:"
# Both GB10 decision models refuse pick-one questions with more options.
DECISION_MAX_CHOICE_OPTIONS: Final = 10


class ModelKey(StrEnum):
    """Catalog entries; the value is the part after ``tuttitrip:``."""

    AGENT = "agent"
    CHAT = "chat"
    DECIDE = "decide"
    DECIDE_LAYA = "decide-laya"
    DECIDE_CLOUD = "decide-cloud"
    OPENROUTER = "openrouter"


def model_id(key: ModelKey) -> str:
    """Model-id string an agent passes as its model.

    Args:
        key: The catalog entry.

    Returns:
        A string such as ``tuttitrip:agent``.
    """
    return f"{MODEL_ID_PREFIX}{key.value}"


def _qwen(name: str, settings: LlmSettings) -> OpenAIChatModel:
    return OpenAIChatModel(
        name,
        provider=OpenAIProvider(
            base_url=settings.gb10_base_url,
            api_key=settings.gb10_api_key.get_secret_value(),
        ),
    )


def _openrouter_key(settings: LlmSettings) -> str | None:
    return (
        settings.openrouter_api_key.get_secret_value()
        or os.environ.get("OPENROUTER_API_KEY")
        or None
    )


def _openrouter(settings: LlmSettings) -> OpenRouterModel:
    return OpenRouterModel(
        settings.openrouter_model,
        provider=OpenRouterProvider(api_key=_openrouter_key(settings)),
    )


def _decision(name: str, base_url: str, api_key: str | None) -> SystemOneModel:
    return SystemOneModel(
        name,
        provider=SystemOneProvider(base_url=base_url, api_key=api_key),
        profile=DecisionModelProfile(
            decision_max_choice_options=DECISION_MAX_CHOICE_OPTIONS
        ),
    )


def _build_agent(settings: LlmSettings) -> Model:
    return FallbackModel(_qwen(settings.agent_model, settings), _openrouter(settings))


def _build_chat(settings: LlmSettings) -> Model:
    return FallbackModel(_qwen(settings.chat_model, settings), _openrouter(settings))


def _build_decide(settings: LlmSettings) -> Model:
    basal = _decision(
        settings.basal_model,
        settings.basal_base_url,
        settings.gb10_api_key.get_secret_value(),
    )
    # A decision model hands off (DecisionHandOff is a ModelAPIError), and the
    # default fallback_on then escalates to the language model.
    return FallbackModel(basal, _qwen(settings.agent_model, settings))


def _build_decide_laya(settings: LlmSettings) -> Model:
    return _decision(
        settings.laya_model,
        settings.laya_base_url,
        settings.gb10_api_key.get_secret_value(),
    )


def _build_decide_cloud(settings: LlmSettings) -> Model:
    return _decision(
        settings.jev_model, settings.jev_base_url, _openrouter_key(settings)
    )


_BUILDERS: Final[dict[ModelKey, Callable[[LlmSettings], Model]]] = {
    ModelKey.AGENT: _build_agent,
    ModelKey.CHAT: _build_chat,
    ModelKey.DECIDE: _build_decide,
    ModelKey.DECIDE_LAYA: _build_decide_laya,
    ModelKey.DECIDE_CLOUD: _build_decide_cloud,
    ModelKey.OPENROUTER: _openrouter,
}


def build_model(key: ModelKey, settings: LlmSettings) -> Model:
    """Build the real model for a catalog entry from settings.

    Args:
        key: The catalog entry.
        settings: LLM settings (endpoints, keys, model names).

    Returns:
        A Pydantic AI model; network calls happen only when it is used.
    """
    return _BUILDERS[key](settings)


class ModelCatalog:
    """Resolves ``tuttitrip:<key>`` ids to lazily built, cached models."""

    def __init__(self) -> None:
        self._models: dict[ModelKey, Model] = {}
        self._override: Model | None = None

    def get(self, key: ModelKey) -> Model:
        """Return the model for an entry (or the test override).

        Args:
            key: The catalog entry.

        Returns:
            The cached model.
        """
        if self._override is not None:
            return self._override
        if key not in self._models:
            self._models[key] = build_model(key, get_settings().llm)
        return self._models[key]

    def resolve(self, _ctx: ModelResolutionContext[Any], model_id: str) -> Model | None:
        """``ResolveModelId`` hook: map our ids to models, ignore others.

        Args:
            _ctx: Resolution context (unused; models do not depend on deps).
            model_id: The id the agent run asked for.

        Returns:
            The model, or ``None`` to let Pydantic AI resolve foreign ids.
        """
        if not model_id.startswith(MODEL_ID_PREFIX):
            return None
        return self.get(ModelKey(model_id.removeprefix(MODEL_ID_PREFIX)))

    def capability(self) -> ResolveModelId[Any]:
        """Capability to attach to every agent that uses this catalog.

        Returns:
            A ``ResolveModelId`` capability bound to this catalog.
        """
        return ResolveModelId(self.resolve)

    @contextmanager
    def override(self, model: Model) -> Generator[None]:
        """Resolve every entry to ``model`` (tests).

        Args:
            model: Replacement, e.g. ``TestModel()``.

        Yields:
            Nothing; the override ends with the ``with`` block.
        """
        previous, self._override = self._override, model
        try:
            yield
        finally:
            self._override = previous


catalog = ModelCatalog()
"""Process-wide catalog used by every agent."""
