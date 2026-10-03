"""Mirror of the worker contract. Canonical: tuttitrip-worker ``contracts.py``.

Both repositories render the same document with :func:`contract_json` and
commit it as ``contracts/jobs.schema.json``. CI compares the two files.
Rules (deploy/CONVENTIONS.md, "Integracja z workerem"):

* every payload carries ``contract_version``;
* payloads are small (ids and parameters), sent as portable JSON;
* change order: worker accepts old+new, backend switches, worker drops old.
"""

import json
from enum import StrEnum
from typing import NamedTuple
from uuid import UUID

from pydantic import BaseModel, Field

CONTRACT_VERSION = 1
WORKER_APPLICATION_NAME = "tuttitrip-worker"
PROGRESS_EVENT = "progress"


class Queue(StrEnum):
    """DBOS queues served by the worker."""

    PLANNING = "planning"
    SYSTEM = "system"


class Workflow(StrEnum):
    """Registered DBOS workflow names in the worker."""

    GENERATE_TRIP_PLAN = "generate_trip_plan"
    PING = "ping"


class ContractPayload(BaseModel):
    """Base for every input and output: carries the contract version."""

    contract_version: int = CONTRACT_VERSION


class GenerateTripPlanInput(ContractPayload):
    """Input of ``generate_trip_plan``."""

    trip_id: UUID
    request: str = Field(min_length=1, max_length=4000)


class GenerateTripPlanOutput(ContractPayload):
    """Output of ``generate_trip_plan`` (the full plan goes to ``job_results``)."""

    destination: str
    days: int
    highlights: list[str]


class PingInput(ContractPayload):
    """Input of ``ping``: an echo used by post-deploy smoke tests (no LLM)."""

    message: str = Field(max_length=200)


class PingOutput(ContractPayload):
    """Output of ``ping``."""

    message: str
    worker_app_version: str


class Progress(BaseModel):
    """Value of the ``progress`` event."""

    stage: str
    percent: int = Field(ge=0, le=100)


class WorkflowSpec(NamedTuple):
    """Queue and payload models of one workflow."""

    queue: Queue
    input: type[ContractPayload]
    output: type[ContractPayload]


WORKFLOWS: dict[Workflow, WorkflowSpec] = {
    Workflow.GENERATE_TRIP_PLAN: WorkflowSpec(
        Queue.PLANNING, GenerateTripPlanInput, GenerateTripPlanOutput
    ),
    Workflow.PING: WorkflowSpec(Queue.SYSTEM, PingInput, PingOutput),
}

EVENTS: dict[str, type[BaseModel]] = {PROGRESS_EVENT: Progress}

type JSON = dict[str, JSON] | list[JSON] | str | int | float | bool | None


def _strip_docs(node: JSON) -> JSON:
    """Drop ``title``/``description`` so docstrings never cause drift.

    Args:
        node: Any JSON value.

    Returns:
        The same value without documentation keys.
    """
    if isinstance(node, dict):
        return {
            k: _strip_docs(v)
            for k, v in node.items()
            if k not in {"title", "description"}
        }
    if isinstance(node, list):
        return [_strip_docs(v) for v in node]
    return node


def _schema(model: type[BaseModel]) -> JSON:
    return _strip_docs(model.model_json_schema())


def _workflow_entry(spec: WorkflowSpec) -> dict[str, JSON]:
    return {
        "queue": spec.queue.value,
        "input": _schema(spec.input),
        "output": _schema(spec.output),
    }


def contract_document() -> dict[str, JSON]:
    """Build the language-neutral description of the contract.

    Returns:
        Versions, names and JSON Schemas of all payloads and events.
    """
    queues: list[JSON] = [q.value for q in sorted(Queue)]
    workflows: dict[str, JSON] = {
        name.value: _workflow_entry(spec) for name, spec in WORKFLOWS.items()
    }
    events: dict[str, JSON] = {name: _schema(model) for name, model in EVENTS.items()}
    return {
        "contract_version": CONTRACT_VERSION,
        "application_name": WORKER_APPLICATION_NAME,
        "queues": queues,
        "workflows": workflows,
        "events": events,
    }


def contract_json() -> str:
    """Render the contract exactly as committed in ``contracts/jobs.schema.json``.

    Returns:
        Pretty-printed JSON with sorted keys and a trailing newline.
    """
    return (
        json.dumps(contract_document(), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    )
