"""Evaluate requirements against an offer without guessing."""

from tuttitrip.accommodation.schemas import (
    OfferFeatures,
    Requirement,
    RequirementCheck,
    RequirementStatus,
)


def evaluate(requirement: Requirement, offer: OfferFeatures) -> RequirementCheck:
    """Classify one requirement; unknown stays ``unconfirmed``.

    Args:
        requirement: The must-have feature.
        offer: Known offer features.

    Returns:
        The requirement's status.
    """
    if requirement.feature in offer.present:
        status = RequirementStatus.MET
    elif requirement.feature in offer.absent:
        status = RequirementStatus.UNMET
    else:
        status = RequirementStatus.UNCONFIRMED
    return RequirementCheck(feature=requirement.feature, status=status)
