"""Score candidate plans."""

from tuttitrip.planning.fairness.logic.welfare import welfare
from tuttitrip.planning.fairness.schemas import FairnessRequest, FairnessScore


def score_plan(request: FairnessRequest) -> FairnessScore:
    """Compute the fairness objective for one plan.

    Args:
        request: Utilities and weights of every person.

    Returns:
        The objective value.
    """
    return FairnessScore(
        score=welfare(
            ((p.utility, p.weight) for p in request.people),
            1.0 if request.alpha is None else request.alpha,
        )
    )
