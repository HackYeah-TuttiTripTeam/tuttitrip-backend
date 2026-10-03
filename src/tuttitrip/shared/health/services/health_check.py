"""Compose the readiness report."""

from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import ping
from tuttitrip.shared.health.schemas import HealthResponse


async def check_health() -> HealthResponse:
    """Check the database and report the environment name.

    Returns:
        ``ok`` when the database answers, ``degraded`` otherwise.
    """
    database_ok = await ping()
    return HealthResponse(
        status="ok" if database_ok else "degraded",
        database="ok" if database_ok else "unavailable",
        environment=get_settings().environment,
    )
