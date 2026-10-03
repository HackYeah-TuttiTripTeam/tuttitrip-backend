"""Interview agent: picks the next card to show the organizer."""

from pydantic_ai import Agent

from tuttitrip.interview.schemas import InterviewCard
from tuttitrip.shared.llm.services.model_catalog import ModelKey, catalog, model_id

interview_agent: Agent[None, InterviewCard] = Agent(
    model_id(ModelKey.AGENT),
    capabilities=[catalog.capability()],
    output_type=InterviewCard,
    instructions=(
        "You interview a trip organizer about their group. Ask one short "
        "question at a time and pick the card type that fits it best."
    ),
    defer_model_check=True,
)
