"""Interview agent: picks the next card to show the organizer."""

from pydantic_ai import Agent

from tuttitrip.interview.schemas import InterviewCard
from tuttitrip.shared.config.settings import get_settings

interview_agent: Agent[None, InterviewCard] = Agent(
    get_settings().llm.model,
    output_type=InterviewCard,
    instructions=(
        "You interview a trip organizer about their group. Ask one short "
        "question at a time and pick the card type that fits it best."
    ),
    defer_model_check=True,
)
