"""Enqueue, inspect and cancel worker workflows through ``DBOSClient``.

The backend never executes workflows. It writes them to the DBOS system
database, where the worker (application ``tuttitrip-worker``) dequeues them.
"""

import hashlib
import json
from typing import Protocol

from dbos import DBOSClient, EnqueueOptions, WorkflowSerializationFormat
from dbos import error as dbos_error
from sqlalchemy.exc import SQLAlchemyError

from tuttitrip.shared.jobs.contracts import (
    PROGRESS_EVENT,
    WORKFLOWS,
    ContractPayload,
    Progress,
    Queue,
    Workflow,
)
from tuttitrip.shared.jobs.schemas import JobState

# Start-to-close limits; DBOS cancels a workflow that runs longer.
TIMEOUT_SECONDS: dict[Workflow, float] = {
    Workflow.GENERATE_TRIP_PLAN: 900.0,
    Workflow.EMBED_TEXTS: 300.0,
    Workflow.PING: 60.0,
}

_UNAVAILABLE = (SQLAlchemyError, dbos_error.DBOSException, OSError)


class JobNotFoundError(Exception):
    """No workflow with this id."""


class JobQueueUnavailableError(Exception):
    """The DBOS system database is unreachable or not migrated."""


def workflow_id_for(workflow: Workflow, key: str, payload: ContractPayload) -> str:
    """Deterministic id: the same request never starts a second run.

    Args:
        workflow: Workflow to run.
        key: Domain id the job is about (e.g. the trip id).
        payload: Input payload; its canonical JSON is hashed.

    Returns:
        ``<workflow>-<key>-<16 hex chars>``.
    """
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return f"{workflow.value}-{key}-{digest}"


class JobQueue(Protocol):
    """What the API needs from the job system (a fake implements it in tests)."""

    async def enqueue(
        self,
        workflow: Workflow,
        payload: ContractPayload,
        *,
        user: str,
        key: str,
        queue: Queue | None = None,
    ) -> str:
        """Enqueue (idempotently) on ``queue`` or the default one; return the id."""
        ...

    async def get(self, workflow_id: str) -> JobState:
        """Return the workflow's state."""
        ...

    async def cancel(self, workflow_id: str) -> None:
        """Cancel the workflow if it has not finished."""
        ...


class DbosJobQueue:
    """``JobQueue`` backed by a lazily connecting ``DBOSClient``."""

    def __init__(
        self, system_database_url: str, application_name: str, application_version: str
    ) -> None:
        self._client = DBOSClient(
            system_database_url=system_database_url,
            application_name=application_name,
            lazy=True,
        )
        self._version = application_version

    async def enqueue(
        self,
        workflow: Workflow,
        payload: ContractPayload,
        *,
        user: str,
        key: str,
        queue: Queue | None = None,
    ) -> str:
        """Enqueue with a deterministic id, portable JSON and the env version.

        Args:
            workflow: Workflow to run.
            payload: Input model, sent as one JSON object.
            user: Auth0 subject recorded as the workflow's authenticated user.
            key: Domain id used in the workflow id.
            queue: Queue override (e.g. ``queue_for(provider)``); defaults to
                the workflow's queue in the contract.

        Returns:
            The workflow id (an existing one if the same request was sent before).
        """
        options: EnqueueOptions = {
            "workflow_name": workflow.value,
            "queue_name": (queue or WORKFLOWS[workflow].queue).value,
            "workflow_id": workflow_id_for(workflow, key, payload),
            "workflow_id_reuse_policy": "return-existing",
            # Workers only dequeue their own version; both sides read it from
            # the env file (deploy/CONVENTIONS.md).
            "app_version": self._version,
            # Pickle (the default) would require the worker to have our classes.
            "serialization_type": WorkflowSerializationFormat.PORTABLE,
            "workflow_timeout": TIMEOUT_SECONDS[workflow],
            "authenticated_user": user,
        }
        try:
            handle = await self._client.enqueue_async(
                options, payload.model_dump(mode="json")
            )
        except _UNAVAILABLE as exc:
            raise JobQueueUnavailableError(str(exc)) from exc
        return handle.get_workflow_id()

    async def get(self, workflow_id: str) -> JobState:
        """Read status, output, error and the latest ``progress`` event.

        Args:
            workflow_id: Workflow id returned by ``enqueue``.

        Returns:
            The workflow's state.
        """
        try:
            handle = await self._client.retrieve_workflow_async(workflow_id)
            status = await handle.get_status()
            progress: object = await self._client.get_event_async(
                workflow_id, PROGRESS_EVENT, timeout_seconds=0
            )
        except dbos_error.DBOSNonExistentWorkflowError as exc:
            raise JobNotFoundError(workflow_id) from exc
        except _UNAVAILABLE as exc:
            raise JobQueueUnavailableError(str(exc)) from exc
        return JobState(
            workflow_id=status.workflow_id,
            workflow_name=status.name,
            status=status.status,
            owner=status.authenticated_user,
            output=status.output if isinstance(status.output, dict) else None,
            error=str(status.error) if status.error is not None else None,
            progress=Progress.model_validate(progress) if progress else None,
        )

    async def cancel(self, workflow_id: str) -> None:
        """Cancel a workflow (no-op once it has finished).

        Args:
            workflow_id: Workflow id.
        """
        try:
            await self._client.cancel_workflow_async(workflow_id)
        except _UNAVAILABLE as exc:
            raise JobQueueUnavailableError(str(exc)) from exc
