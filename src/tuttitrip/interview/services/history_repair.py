"""Shaping messages before they are stored: partial turns and secrets."""

from collections.abc import Sequence

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai_harness.guardrails.detectors import redact_secrets

from tuttitrip.interview import constants


def settle(partial: Sequence[ModelMessage]) -> list[ModelMessage]:
    """Make what a failed turn left usable as history.

    A trailing response whose tool calls got no result is dropped, and a history
    that would end on a request is closed with a short assistant note, so the
    next run starts from a valid conversation.

    Args:
        partial: The new messages of the failed run.

    Returns:
        The messages to store; empty when nothing happened.
    """
    kept = list(partial)
    while (
        kept
        and isinstance(kept[-1], ModelResponse)
        and any(isinstance(p, ToolCallPart) for p in kept[-1].parts)
    ):
        kept.pop()
    if kept and isinstance(kept[-1], ModelRequest):
        kept.append(ModelResponse(parts=[TextPart(constants.INTERRUPTED_NOTE)]))
    return kept


def redact(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """Remove credentials from what the host said, as the text interview does.

    Args:
        messages: A transcript (voice).

    Returns:
        The messages with secrets in user text replaced by placeholders.
    """
    out: list[ModelMessage] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            parts = [_clean(part) for part in message.parts]
            message = ModelRequest(  # ruff: ignore[redefined-loop-name] same message, cleaned parts
                parts=parts,
                instructions=message.instructions,
                metadata=message.metadata,
            )
        out.append(message)
    return out


def _clean(part: ModelRequestPart) -> ModelRequestPart:
    if isinstance(part, UserPromptPart) and isinstance(part.content, str):
        result = redact_secrets(part.content)
        if result.action == "replace" and isinstance(result.replacement, str):
            return UserPromptPart(content=result.replacement, timestamp=part.timestamp)
    return part
