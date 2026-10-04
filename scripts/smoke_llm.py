"""Live smoke test of the model catalog (real calls, never run in CI).

Needs ``TUTTITRIP_LLM__GB10_API_KEY`` and ``OPENROUTER_API_KEY`` in the
environment (on the host: ``set -a; . ~/tuttitrip/app.env; set +a``)::

    uv run python scripts/smoke_llm.py [entry ...]

Entries: agent, chat, decide, decide-laya, decide-cloud, openrouter. Without
arguments it checks every entry. Each link of a fallback chain is called on its
own and labelled with its model name, because an answer from the whole chain
would not prove that the first link works. Exits non-zero if any link fails.
"""

import asyncio
import sys
import time
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.system_one import SystemOneModel

from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.llm.services.model_catalog import ModelKey, model_id, model_links


class Reply(BaseModel):
    """Polish one-liner."""

    text: str


class Reason(BaseModel):
    """Why the group does not want the trip."""

    reason: Literal["cena", "termin", "miejsce"]


async def _check(key: ModelKey, link: Model) -> bool:
    label = f"{model_id(key)} -> {link.model_name}"
    if isinstance(link, SystemOneModel):
        agent = Agent(
            link,
            output_type=Reason,
            instructions="Choose the reason that best matches the message.",
        )
        prompt = "Za drogo, nie stać nas na ten wyjazd."
    else:
        agent = Agent(
            link,
            output_type=Reply,
            instructions="Odpowiedz jednym zdaniem po polsku.",
        )
        prompt = "Napisz zdanie powitalne dla grupy planującej wyjazd w góry."
    started = time.monotonic()
    try:
        result = await agent.run(prompt)
    except Exception as exc:  # ruff: ignore[blind-except] the smoke test reports every failure
        print(f"FAIL {label}: {type(exc).__name__}: {exc}")
        return False
    print(f"OK   {label} {time.monotonic() - started:.1f}s {result.output!r}")
    return True


async def main(keys: list[ModelKey]) -> int:
    """Run the live checks, one call per chain link.

    Args:
        keys: Catalog entries to check.

    Returns:
        Process exit code: 0 when every link answered.
    """
    llm = get_settings().llm
    results = [
        await _check(key, link) for key in keys for link in model_links(key, llm)
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    selected = [ModelKey(arg) for arg in sys.argv[1:]] or list(ModelKey)
    sys.exit(asyncio.run(main(selected)))
