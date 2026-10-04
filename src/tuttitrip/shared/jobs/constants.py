"""Fixed values of the job system."""

from typing import Final

WORKER_OK: Final = "ok"
"""Worker liveness: the heartbeat is fresh."""

WORKER_STALE: Final = "stale"
"""Worker liveness: the heartbeat is older than ``jobs.worker_stale_after_seconds``."""

WORKER_MISSING: Final = "missing"
"""Worker liveness: no heartbeat, or older than ``worker_missing_after_seconds``."""

LOCAL_PROVIDER: Final = "local"
"""``provider`` value that routes a job to the local GPU queue."""

WORKFLOW_ID_DIGEST_CHARS: Final = 16
"""Hex characters of the payload hash at the end of a workflow id."""

PING_MESSAGE_BYTES: Final = 8
"""Random bytes (hex-encoded) in the message of the smoke-test ping."""
