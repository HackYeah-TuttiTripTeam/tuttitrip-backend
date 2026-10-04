"""Model catalog for Pydantic AI agents (R1: the only way to models).

Agents pass text ids such as ``tuttitrip:agent`` and the catalog's
``ResolveModelId`` capability turns them into models built from settings,
lazily, so the app and the tests run without keys and no real provider is built
until a model is actually used. This mirrors the catalog of tuttitrip-worker
(same ids). A link whose key is missing is skipped, and a chain with no key
at all raises ``UserError``. Tests swap every id for a ``TestModel`` or
``FunctionModel`` with :meth:`ModelCatalog.override`.

| Id | Model |
| --- | --- |
| ``tuttitrip:agent`` | Qwen ``gb10_agent_model`` (thinking, tools), then OpenRouter |
| ``tuttitrip:interview`` | ``interview.agent_model`` on OpenRouter, else ``agent`` |
| ``tuttitrip:chat`` | Qwen ``gb10_chat_model``, then OpenRouter |
| ``tuttitrip:decide`` | basal, escalates to the Qwen chat model |
| ``tuttitrip:decide-laya`` | Laya, escalates to the Qwen chat model |
| ``tuttitrip:decide-cloud`` | JEV on OpenRouter |
| ``tuttitrip:openrouter`` | OpenRouter chat model |
"""

import os
from collections.abc import Generator
from contextlib import contextmanager
from enum import StrEnum
from typing import Any, Final

from pydantic_ai.capabilities import ResolveModelId
from pydantic_ai.exceptions import UserError
from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
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
    INTERVIEW = "interview"
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


def _gb10_key(settings: LlmSettings) -> str | None:
    return settings.gb10_api_key.get_secret_value() or None


def _openrouter_key(settings: LlmSettings) -> str | None:
    return (
        settings.openrouter_api_key.get_secret_value()
        or os.environ.get("OPENROUTER_API_KEY")
        or None
    )


def _qwen(name: str, settings: LlmSettings) -> Model | None:
    key = _gb10_key(settings)
    if key is None:
        return None
    return OpenAIChatModel(
        name,
        provider=OpenAIProvider(base_url=settings.gb10_base_url, api_key=key),
    )


def _openrouter(settings: LlmSettings) -> Model | None:
    key = _openrouter_key(settings)
    if key is None:
        return None
    return OpenRouterModel(
        settings.openrouter_model, provider=OpenRouterProvider(api_key=key)
    )


def _interview(settings: LlmSettings) -> list[Model | None]:
    interview = get_settings().interview
    name = interview.agent_model
    if not name:
        return _qwen_then_openrouter(settings.gb10_agent_model, settings)
    key = _openrouter_key(settings)
    if key is None:
        return []
    return [
        OpenRouterModel(
            name.removeprefix("openrouter:"),
            provider=OpenRouterProvider(api_key=key),
            settings=None
            if interview.agent_thinking
            else OpenRouterModelSettings(openrouter_reasoning={"effort": "none"}),
        )
    ]


def _decision(name: str, base_url: str, api_key: str | None) -> Model | None:
    if api_key is None:
        return None
    return SystemOneModel(
        name,
        provider=SystemOneProvider(base_url=base_url, api_key=api_key),
        profile=DecisionModelProfile(
            decision_max_choice_options=DECISION_MAX_CHOICE_OPTIONS
        ),
    )


def _qwen_then_openrouter(model: str, settings: LlmSettings) -> list[Model | None]:
    return [_qwen(model, settings), _openrouter(settings)]


# ruff: ignore[too-many-return-statements]
def _links(key: ModelKey, settings: LlmSettings) -> list[Model | None]:
    gb10 = _gb10_key(settings)
    match key:
        case ModelKey.AGENT:
            return _qwen_then_openrouter(settings.gb10_agent_model, settings)
        case ModelKey.INTERVIEW:
            return _interview(settings)
        case ModelKey.CHAT:
            return _qwen_then_openrouter(settings.gb10_chat_model, settings)
        # A decision model hands off (DecisionHandOff is a ModelAPIError) and the
        # default fallback_on then escalates to the chat model (no thinking).
        case ModelKey.DECIDE:
            return [
                _decision(settings.basal_model, settings.basal_base_url, gb10),
                _qwen(settings.gb10_chat_model, settings),
            ]
        case ModelKey.DECIDE_LAYA:
            return [
                _decision(settings.laya_model, settings.laya_base_url, gb10),
                _qwen(settings.gb10_chat_model, settings),
            ]
        case ModelKey.DECIDE_CLOUD:
            return [
                _decision(
                    settings.jev_model,
                    settings.openrouter_base_url,
                    _openrouter_key(settings),
                )
            ]
        case ModelKey.OPENROUTER:
            return [_openrouter(settings)]


def model_links(key: ModelKey, settings: LlmSettings) -> list[Model]:
    """Build the models of an entry's fallback chain, skipping links without a key.

    Args:
        key: The catalog entry.
        settings: LLM settings (endpoints, keys, model names).

    Returns:
        The usable links in fallback order (network calls happen only on use).

    Raises:
        UserError: When no link of the chain has its key.
    """
    links = [link for link in _links(key, settings) if link is not None]
    if not links:
        message = (
            f"No API key for {model_id(key)}: set TUTTITRIP_LLM__GB10_API_KEY"
            " or OPENROUTER_API_KEY as the entry needs."
        )
        raise UserError(message)
    return links


def build_model(key: ModelKey, settings: LlmSettings) -> Model:
    """Build the real model for a catalog entry from settings.

    Args:
        key: The catalog entry.
        settings: LLM settings (endpoints, keys, model names).

    Returns:
        The single link, or a ``FallbackModel`` over the links that have a key.
    """
    links = model_links(key, settings)
    return links[0] if len(links) == 1 else FallbackModel(*links)


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

    def resolve(
        self, _ctx: ModelResolutionContext[Any], requested: str
    ) -> Model | None:
        """``ResolveModelId`` hook: map our ids to models, ignore others.

        Args:
            _ctx: Resolution context (unused; models do not depend on deps).
            requested: The id the agent run asked for.

        Returns:
            The model, or ``None`` to let Pydantic AI resolve foreign ids.
        """
        if not requested.startswith(MODEL_ID_PREFIX):
            return None
        name = requested.removeprefix(MODEL_ID_PREFIX)
        try:
            key = ModelKey(name)
        except ValueError:
            known = ", ".join(model_id(key) for key in ModelKey)
            message = f"Unknown model id {requested!r}; known ids: {known}."
            raise UserError(message) from None
        return self.get(key)

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
