"""Planning: planner agent (no network), fairness objective, plan linter."""

import asyncio
import math
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from tuttitrip.main import create_app
from tuttitrip.planning.fairness.logic.welfare import weighted_log_welfare
from tuttitrip.planning.linter.logic.rules import lint
from tuttitrip.planning.linter.schemas import LintRequest, PlanItem
from tuttitrip.planning.schemas import TripPlan
from tuttitrip.planning.services.planner_agent import planner_agent


def test_planner_agent_returns_trip_plan() -> None:
    model = TestModel(
        custom_output_args={
            "destination": "Kraków",
            "days": 3,
            "highlights": ["Wawel", "Kazimierz"],
        },
    )
    with planner_agent.override(model=model):
        # run() under asyncio.run: run_sync leaves its event loop unclosed.
        result = asyncio.run(planner_agent.run("Weekend w Krakowie"))
    assert result.output == TripPlan(
        destination="Kraków", days=3, highlights=["Wawel", "Kazimierz"]
    )


def test_log_welfare_prefers_the_balanced_plan() -> None:
    # The plan's own example: same total (100), plan B is fairer.
    plan_a = weighted_log_welfare([(90, 1), (10, 1)])
    plan_b = weighted_log_welfare([(55, 1), (45, 1)])
    assert plan_b > plan_a
    assert math.isclose(plan_a, math.log1p(90) + math.log1p(10))


def test_weights_multiply_log_utility() -> None:
    assert math.isclose(weighted_log_welfare([(9, 2)]), 2 * math.log(10))


def test_linter_flags_over_budget_plan() -> None:
    request = LintRequest(
        items=[
            PlanItem(name="Muzeum", cost=Decimal(80)),
            PlanItem(name="Zoo", cost=Decimal(50)),
        ],
        budget=Decimal(100),
    )
    assert [v.rule for v in lint(request)] == ["budget"]
    assert lint(request.model_copy(update={"budget": Decimal(130)})) == []


def test_fairness_endpoint() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/planning/fairness/score",
            json={"people": [{"utility": 9, "weight": 1}]},
        )
    assert response.status_code == 200
    assert math.isclose(response.json()["score"], math.log(10))
