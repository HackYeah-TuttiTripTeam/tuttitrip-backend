"""Interview agent: tools write the shared AG-UI state."""

import asyncio

from pydantic_ai.models.test import TestModel

from tuttitrip.interview.services.interview_agent import interview_agent, new_deps


def test_remember_fills_state() -> None:
    deps = new_deps()
    model = TestModel(call_tools=["remember"])
    with interview_agent.override(model=model):
        asyncio.run(interview_agent.run("Gdańsk", deps=deps))
    assert [f.key for f in deps.state.facts] == ["a"]
