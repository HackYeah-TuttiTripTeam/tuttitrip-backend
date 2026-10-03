"""Linter endpoints."""

from fastapi import APIRouter

from tuttitrip.planning.linter.schemas import LintReport, LintRequest
from tuttitrip.planning.linter.services import linter_service
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature

router = APIRouter(prefix="/planning/linter", tags=["planning"])


@router.post("/check", dependencies=[requires(Feature.PLANNING_LINTER, Access.READ)])
def check(request: LintRequest) -> LintReport:
    """Lint a plan.

    Args:
        request: Plan and budget.

    Returns:
        All violations.
    """
    return linter_service.check_plan(request)
