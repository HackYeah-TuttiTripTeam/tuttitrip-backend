"""Live smoke test of the model catalog (real calls, never run in CI).

Needs ``TUTTITRIP_LLM__GB10_API_KEY`` and ``OPENROUTER_API_KEY`` in the
environment (on the host: ``set -a; . ~/tuttitrip/app.env; set +a``)::

    uv run python scripts/smoke_llm.py [entry ...]

Entries: agent, chat, decide, decide-laya, decide-cloud, openrouter. Without
arguments it checks every entry and exits non-zero if any fails.
"""

import asyncio
import sys
import time
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent

from tuttitrip.shared.llm.services.model_catalog import ModelKey, catalog, model_id


class Reply(BaseModel):
    """Polish one-liner."""

    text: str


class Reason(BaseModel):
    """Why the group does not want the trip."""

    reason: Literal["cena", "termin", "miejsce"]


def _agent(key: ModelKey) -> Agent[None, BaseModel]:
    decision = key.value.startswith("decide")
    if decision:
        return Agent(
            model_id(key),
            output_type=Reason,
            instructions="Choose the reason that best matches the message.",
            capabilities=[catalog.capability()],
        )
    return Agent(
        model_id(key),
        output_type=Reply,
        instructions="Odpowiedz jednym zdaniem po polsku.",
        capabilities=[catalog.capability()],
    )


async def _check(key: ModelKey) -> bool:
    prompt = "Za drogo, nie stać nas na ten wyjazd."
    if not key.value.startswith("decide"):
        prompt = "Napisz zdanie powitalne dla grupy planującej wyjazd w góry."
    started = time.monotonic()
    try:
        result = await _agent(key).run(prompt)
    except Exception as exc:  # ruff: ignore[blind-except] the smoke test reports every failure
        print(f"FAIL {key.value}: {type(exc).__name__}: {exc}")
        return False
    elapsed = time.monotonic() - started
    print(f"OK   {key.value} {elapsed:.1f}s {result.output!r}")
    return True


async def main(keys: list[ModelKey]) -> int:
    """Run the live checks.

    Args:
        keys: Catalog entries to check.

    Returns:
        Process exit code: 0 when every entry answered.
    """
    results = [await _check(key) for key in keys]
    return 0 if all(results) else 1


if __name__ == "__main__":
    selected = [ModelKey(arg) for arg in sys.argv[1:]] or list(ModelKey)
    sys.exit(asyncio.run(main(selected)))
