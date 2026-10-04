"""Reproducible plan hash (E5 tie-break)."""

import hashlib
import json

PLAN_HASH_LENGTH = 12


def compute_plan_hash(content: object) -> str:
    """Hash a plan's content.

    Args:
        content: JSON-serializable plan content without ids and timestamps.

    Returns:
        First 12 hex characters of the SHA-256 of the canonical JSON.
    """
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:PLAN_HASH_LENGTH]
