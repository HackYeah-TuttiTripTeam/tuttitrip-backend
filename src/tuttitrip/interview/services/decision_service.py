"""Decision agents of the interview: free text in, one value of a closed list out.

A decision model (basal) answers a pick-one question and reports how sure it
is. It writes no text. Tools use the pick only above the confidence threshold;
below it they ask the host to confirm on a card.
"""

from dataclasses import dataclass

from pydantic import BaseModel
from pydantic_ai import Agent

from tuttitrip.interview import constants
from tuttitrip.interview.schemas import ConstraintKind
from tuttitrip.places.schemas import DietTag
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.llm.services.model_catalog import ModelKey, catalog, model_id


class DietPick(BaseModel):
    """The diet the host's sentence names."""

    diet: DietTag


class ConstraintPick(BaseModel):
    """The access limit the host's sentence names."""

    kind: ConstraintKind


@dataclass(frozen=True)
class Classification[T]:
    """A pick and how sure the model was (``None``: it did not say)."""

    value: T
    confidence: float | None

    @property
    def sure(self) -> bool:
        """Whether the pick may be saved without asking.

        Returns:
            True above the configured confidence; a model that reports no
            confidence (a language model that took over) counts as sure.
        """
        floor = get_settings().interview.classify_min_confidence
        return self.confidence is None or self.confidence >= floor


diet_agent: Agent[None, DietPick] = Agent(
    model_id(ModelKey.DECIDE),
    capabilities=[catalog.capability()],
    output_type=DietPick,
    instructions="Which diet does the sentence of the trip organizer name?",
    defer_model_check=True,
)
constraint_agent: Agent[None, ConstraintPick] = Agent(
    model_id(ModelKey.DECIDE),
    capabilities=[catalog.capability()],
    output_type=ConstraintPick,
    instructions="Which access limit does the sentence of the trip organizer name?",
    defer_model_check=True,
)


def _confidence(details: dict[str, object] | None) -> float | None:
    raw = (details or {}).get(constants.STATE_CONFIDENCE_KEY)
    if isinstance(raw, dict):
        values = [v for v in raw.values() if isinstance(v, int | float)]
        return float(min(values)) if values else None
    return float(raw) if isinstance(raw, int | float) else None


async def classify_diet(text: str) -> Classification[DietTag]:
    """Classify a sentence into one diet.

    Args:
        text: What the host said about the diet.

    Returns:
        The diet and the model's confidence.
    """
    result = await diet_agent.run(text[: constants.MAX_FREE_TEXT])
    confidence = _confidence(result.response.provider_details)
    return Classification(result.output.diet, confidence)


async def classify_constraint(text: str) -> Classification[ConstraintKind]:
    """Classify a sentence into one access limit.

    Args:
        text: What the host said about the limit.

    Returns:
        The limit and the model's confidence.
    """
    result = await constraint_agent.run(text[: constants.MAX_FREE_TEXT])
    confidence = _confidence(result.response.provider_details)
    return Classification(result.output.kind, confidence)
