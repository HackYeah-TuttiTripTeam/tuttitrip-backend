"""``/health`` (readiness, checks the DB) and ``/health/live`` (liveness)."""

from fastapi import APIRouter, Response, status

from tuttitrip.shared.health.constants import STATUS_OK
from tuttitrip.shared.health.schemas import HealthResponse, LiveResponse
from tuttitrip.shared.health.services.health_check import check_health
from tuttitrip.shared.permissions.api import public

router = APIRouter(prefix="/health", tags=["health"], dependencies=[public()])


@router.get("")
async def health(response: Response) -> HealthResponse:
    """Readiness: 200 when the database answers, 503 otherwise.

    Args:
        response: Used to set the status code.

    Returns:
        The health report.
    """
    report = await check_health()
    if report.status != STATUS_OK:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report


@router.get("/live")
async def live() -> LiveResponse:
    """Liveness: the process serves HTTP (used by the Docker healthcheck).

    Returns:
        A constant ``ok``.
    """
    return LiveResponse()
