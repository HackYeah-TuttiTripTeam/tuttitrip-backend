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
        request: Utilities and weights of every person, and optionally the
            fairness slider ``alpha`` (0 to 3; omitted means 1, the weighted log).

    Returns:
        The group welfare ``W``.
    """
    return fairness_service.score_plan(request)
