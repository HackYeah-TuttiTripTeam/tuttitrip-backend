"""Trip planner agent (S2: writes drafts, never decides)."""

from pydantic_ai import Agent

from tuttitrip.planning.schemas import TripPlan
from tuttitrip.shared.llm.services.model_catalog import ModelKey, catalog, model_id

planner_agent: Agent[None, TripPlan] = Agent(
    model_id(ModelKey.AGENT),
    capabilities=[catalog.capability()],
    output_type=TripPlan,
    instructions=(
        "You are a travel planner. Suggest a short trip plan "
        "matching the user's request."
    ),
    # Resolve the model lazily, so importing this module never needs API keys.
    defer_model_check=True,
)
