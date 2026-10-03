"""Check an offer against the whole contract."""

from collections.abc import Iterable

from tuttitrip.accommodation.logic.contract import evaluate
from tuttitrip.accommodation.schemas import OfferFeatures, Requirement, RequirementCheck


def check_offer(
    requirements: Iterable[Requirement], offer: OfferFeatures
) -> list[RequirementCheck]:
    """Evaluate every requirement.

    Args:
        requirements: The contract.
        offer: Known offer features.

    Returns:
        One check per requirement.
    """
    return [evaluate(requirement, offer) for requirement in requirements]
