"""A stored plan version as the response model.

Pure: the columns of a ``plan_versions`` row come in as plain values. Rows
stored before a key existed (``upgrades``, ``transit``) read with its empty default.
"""

import datetime as dt
from typing import Any
from uuid import UUID

from tuttitrip.planning.plans.schemas import PlanParams, PlanRead

RESULT_KEYS = (
    "days",
    "lodging",
    "fairness",
    "floors_missed",
    "violation",
    "conflicts",
    "explain",
    "verdicts",
    "budget",
    "upgrades",
    "transit",
    "transit_tickets",
    "telemetry",
)
_OPTIONAL: dict[str, Any] = {"upgrades": [], "transit": None, "transit_tickets": []}


def stored_plan(  # ruff: ignore[too-many-arguments] the columns of one row
    *,
    plan_id: UUID,
    trip_id: UUID,
    version: int,
    input_hash: str,
    plan_hash: str,
    created_at: dt.datetime,
    params: dict[str, Any],
    result: dict[str, Any],
) -> PlanRead:
    """Build the response from the columns of a stored version.

    Args:
        plan_id: Version id.
        trip_id: Trip id.
        version: Version number.
        input_hash: SHA-256 of the input.
        plan_hash: Reproducible plan hash.
        created_at: When the version was stored.
        params: The knobs the plan was computed with.
        result: The copy of the content made when it was computed.

    Returns:
        The plan, unfiltered (every person's ``explain`` card).
    """
    return PlanRead.model_validate(
        {
            "id": plan_id,
            "trip_id": trip_id,
            "version": version,
            "input_hash": input_hash,
            "plan_hash": plan_hash,
            "created_at": created_at,
            "params": PlanParams.model_validate(params),
            **{k: result.get(k, _OPTIONAL.get(k)) for k in RESULT_KEYS},
        }
    )
