"""Is a compatible worker alive in this environment? (heartbeat table)."""

from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.jobs import db
from tuttitrip.shared.jobs.contracts import CONTRACT_VERSION
from tuttitrip.shared.jobs.schemas import WorkerLiveness


class WorkerUnavailableError(Exception):
    """No worker has reported recently; enqueued jobs would not run."""


async def get_worker_liveness(session: AsyncSession) -> WorkerLiveness:
    """Classify the newest heartbeat as ok, stale or missing.

    Args:
        session: Open session.

    Returns:
        Liveness plus contract compatibility.
    """
    settings = get_settings()
    try:
        beat = await db.select_latest_heartbeat(session, settings.environment)
    except SQLAlchemyError:
        beat = None
    if beat is None:
        return WorkerLiveness(
            status="missing", backend_contract_version=CONTRACT_VERSION
        )
    age = (datetime.now(UTC) - beat.last_seen).total_seconds()
    if age <= settings.jobs.worker_stale_after_seconds:
        status = "ok"
    elif age <= settings.jobs.worker_missing_after_seconds:
        status = "stale"
    else:
        status = "missing"
    low, high = beat.min_contract_version, beat.contract_version
    return WorkerLiveness(
        status=status,
        backend_contract_version=CONTRACT_VERSION,
        worker_contract_version=beat.contract_version,
        worker_min_contract_version=beat.min_contract_version,
        worker_app_version=beat.app_version,
        last_seen=beat.last_seen,
        compatible=low <= CONTRACT_VERSION <= high,
    )


async def ensure_worker_available(session: AsyncSession) -> None:
    """Refuse to enqueue when no compatible worker is around.

    Args:
        session: Open session.
    """
    liveness = await get_worker_liveness(session)
    if liveness.status == "missing":
        msg = (
            "No tuttitrip-worker has reported a heartbeat in this environment "
            f"for over {get_settings().jobs.worker_missing_after_seconds} s; "
            "jobs would not run. Try again later."
        )
        raise WorkerUnavailableError(msg)
    if liveness.compatible is False:
        msg = (
            "The worker speaks contract versions "
            f"{liveness.worker_min_contract_version}-{liveness.worker_contract_version},"
            f" the backend speaks {CONTRACT_VERSION}."
        )
        raise WorkerUnavailableError(msg)
