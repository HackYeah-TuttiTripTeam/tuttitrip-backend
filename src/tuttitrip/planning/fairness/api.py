"""Fairness endpoints."""

from fastapi import APIRouter

from tuttitrip.planning.fairness.schemas import FairnessRequest, FairnessScore
from tuttitrip.planning.fairness.services import fairness_service
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature

router = APIRouter(prefix="/planning/fairness", tags=["planning"])


@router.post("/score", dependencies=[requires(Feature.PLANNING_FAIRNESS, Access.READ)])
def score(request: FairnessRequest) -> FairnessScore:
    """Score one candidate plan.

    Args:
        request: Utilities and weights of every person.

    Returns:
        The weighted log welfare.
    """
    return fairness_service.score_plan(request)
