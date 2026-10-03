"""Trip planner agent (S2: writes drafts, never decides)."""

from pydantic_ai import Agent

from tuttitrip.planning.schemas import TripPlan
from tuttitrip.shared.config.settings import get_settings

planner_agent: Agent[None, TripPlan] = Agent(
    get_settings().llm.model,
    output_type=TripPlan,
    instructions=(
        "You are a travel planner. Suggest a short trip plan "
        "matching the user's request."
    ),
    # Resolve the model lazily, so importing this module never needs API keys.
    defer_model_check=True,
)
