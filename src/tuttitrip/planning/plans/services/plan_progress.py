"""Where the plan computation of each trip is, for the client to poll.

The plan is computed inside the request (``POST /trips/{id}/plans``), so its
client cannot ask that request how far it got. The computation reports its stage
here instead and ``GET /trips/{id}/plans/progress`` reads it. The API runs as one
process (``uvicorn`` without ``--workers``), so a dict is enough: nothing is
stored, an entry lives exactly as long as the computation, and a process restart
only loses the stage of a run that is lost anyway.
"""

from collections.abc import Generator
from contextlib import contextmanager
from uuid import UUID

from tuttitrip.planning.logic.progress import PlanProgress, ProgressSink

_running: dict[UUID, PlanProgress] = {}


@contextmanager
def track(trip_id: UUID) -> Generator[ProgressSink]:
    """Record the stage of the trip's computation while it runs.

    Args:
        trip_id: The trip being planned.

    Yields:
        The sink to hand to the computation (safe to call from a worker thread).
    """

    def record(event: PlanProgress) -> None:
        _running[trip_id] = event

    try:
        yield record
    finally:
        _running.pop(trip_id, None)


def current(trip_id: UUID) -> PlanProgress | None:
    """The stage the trip's computation is in.

    Args:
        trip_id: Trip id.

    Returns:
        The latest event, or None when nothing is being computed for the trip.
    """
    return _running.get(trip_id)
