"""Accommodation contract: unknown features stay unconfirmed."""

from tuttitrip.accommodation.logic.contract import evaluate
from tuttitrip.accommodation.schemas import (
    OfferFeatures,
    Requirement,
    RequirementStatus,
)

OFFER = OfferFeatures(present={"pool"}, absent={"parking"})


def test_requirement_statuses() -> None:
    assert evaluate(Requirement(feature="pool"), OFFER).status is RequirementStatus.MET
    assert (
        evaluate(Requirement(feature="parking"), OFFER).status
        is RequirementStatus.UNMET
    )
    assert (
        evaluate(Requirement(feature="sauna"), OFFER).status
        is RequirementStatus.UNCONFIRMED
    )
