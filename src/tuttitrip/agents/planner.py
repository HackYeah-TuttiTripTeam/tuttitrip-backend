"""Trip planner agent."""

from pydantic_ai import Agent

from tuttitrip.domain import TripPlan

DEFAULT_MODEL = "openai:gpt-5.2"

planner_agent: Agent[None, TripPlan] = Agent(
    DEFAULT_MODEL,
    output_type=TripPlan,
    instructions=(
        "You are a travel planner. Suggest a short trip plan "
        "matching the user's request."
    ),
    # Resolve the model lazily, so importing this module never needs API keys.
    defer_model_check=True,
)
