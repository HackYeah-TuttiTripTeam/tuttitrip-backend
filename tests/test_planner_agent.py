"""Tests for the trip planner agent (no network, uses TestModel)."""

from pydantic_ai.models.test import TestModel

from tuttitrip.agents import planner_agent
from tuttitrip.domain import TripPlan


def test_planner_agent_returns_trip_plan() -> None:
    """The agent produces a validated TripPlan."""
    model = TestModel(
        custom_output_args={
            "destination": "Kraków",
            "days": 3,
            "highlights": ["Wawel", "Kazimierz"],
        },
    )

    with planner_agent.override(model=model):
        result = planner_agent.run_sync("Weekend w Krakowie")

    assert result.output == TripPlan(
        destination="Kraków",
        days=3,
        highlights=["Wawel", "Kazimierz"],
    )
