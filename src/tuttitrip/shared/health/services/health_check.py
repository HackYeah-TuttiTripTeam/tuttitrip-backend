"""Compose the readiness report."""

from sqlalchemy.exc import SQLAlchemyError

from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import get_sessionmaker, ping
from tuttitrip.shared.health.schemas import HealthResponse
from tuttitrip.shared.jobs.contracts import CONTRACT_VERSION
from tuttitrip.shared.jobs.schemas import WorkerLiveness
from tuttitrip.shared.jobs.services.worker_liveness import get_worker_liveness


async def worker_liveness() -> WorkerLiveness:
    """Read the worker heartbeat in a short-lived session.

    Returns:
        The worker's liveness.
    """
    async with get_sessionmaker()() as session:
        return await get_worker_liveness(session)


async def check_health() -> HealthResponse:
    """Check the database and the worker heartbeat.

    Returns:
        ``degraded`` if the database is down or the worker is incompatible.
    """
    database_ok = await ping()
    worker = WorkerLiveness(status="missing", backend_contract_version=CONTRACT_VERSION)
    if database_ok:
        try:
            worker = await worker_liveness()
        except SQLAlchemyError:
            # Already logged. An unreadable heartbeat is a database problem,
            # not proof that the worker is gone.
            database_ok = False
    healthy = database_ok and worker.compatible is not False
    return HealthResponse(
        status="ok" if healthy else "degraded",
        database="ok" if database_ok else "unavailable",
        environment=get_settings().environment,
        worker=worker.status,
        contract_version=CONTRACT_VERSION,
        worker_contract_version=worker.worker_contract_version,
    )
