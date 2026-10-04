"""Evaluate requirements against an offer without guessing (three states).

``met`` and ``unmet`` need evidence: a verbatim quote of the offer assessed with
enough confidence, the link's domain for a platform, or the host's own answer.
Everything else is ``unconfirmed`` with a reason; silence is never a "no".
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from math import prod
from typing import Final

from tuttitrip.accommodation.logic.keys import RequirementKind
from tuttitrip.accommodation.logic.platforms import platform_of
from tuttitrip.accommodation.schemas import (
    OfferFeatures,
    Requirement,
    RequirementCheck,
    RequirementStatus,
    UnconfirmedReason,
)
from tuttitrip.shared.jobs.contracts import EvidenceQuote

RHO_UNC: Final = 0.4
"""E2 lodging: points of an ``unconfirmed`` requirement (docs/algorytm.md, 6)."""

MIN_CONFIDENCE: Final = 0.3
"""Least decision-model confidence (its margin from the threshold, 0..1) for a
quote to decide ``met`` or ``unmet``. Uncalibrated, like the other parameters."""

_POINTS: Final = {
    RequirementStatus.MET: 1.0,
    RequirementStatus.UNCONFIRMED: RHO_UNC,
    RequirementStatus.UNMET: 0.0,
}
_DECISIVE: Final = {"present": RequirementStatus.MET, "absent": RequirementStatus.UNMET}


@dataclass(frozen=True, slots=True)
class OfferFacts:
    """Everything known about one offer, as inputs of :func:`check_offer`.

    Attributes:
        host: Host of the offer's link; None without a link.
        features: Amenities the host confirmed by hand.
        requested: Keys sent to the worker for quotes.
        evidence: Worker quotes per key; None while the job has not finished.
        failed: The worker job failed, so ``evidence`` will not come.
    """

    host: str | None = None
    features: OfferFeatures = field(default_factory=OfferFeatures)
    requested: Collection[str] = ()
    evidence: Mapping[str, Sequence[EvidenceQuote]] | None = None
    failed: bool = False


def _check(
    requirement: Requirement,
    status: RequirementStatus,
    *,
    quote: str | None = None,
    confidence: float | None = None,
    reason: UnconfirmedReason | None = None,
) -> RequirementCheck:
    return RequirementCheck(
        feature=requirement.feature,
        kind=requirement.kind,
        hard=requirement.hard,
        status=status,
        quote=quote,
        confidence=confidence,
        reason=reason,
    )


def _unconfirmed(
    requirement: Requirement,
    reason: UnconfirmedReason,
    quote: EvidenceQuote | None = None,
) -> RequirementCheck:
    return _check(
        requirement,
        RequirementStatus.UNCONFIRMED,
        quote=None if quote is None else quote.text,
        confidence=None if quote is None else quote.confidence,
        reason=reason,
    )


def evaluate(requirement: Requirement, offer: OfferFeatures) -> RequirementCheck:
    """Classify one requirement by the host's answers; unknown stays ``unconfirmed``.

    Args:
        requirement: The requirement.
        offer: Features the host confirmed present or absent.

    Returns:
        The check (``no_mention`` when the host said nothing about it).
    """
    if requirement.feature in offer.present:
        return _check(requirement, RequirementStatus.MET)
    if requirement.feature in offer.absent:
        return _check(requirement, RequirementStatus.UNMET)
    return _unconfirmed(requirement, UnconfirmedReason.NO_MENTION)


def _best(quotes: Sequence[EvidenceQuote]) -> EvidenceQuote:
    return max(quotes, key=lambda q: q.confidence or 0.0)


def evaluate_evidence(
    requirement: Requirement,
    quotes: Sequence[EvidenceQuote],
    *,
    min_confidence: float = MIN_CONFIDENCE,
) -> RequirementCheck:
    """Classify one requirement by the worker's quotes.

    Quotes judged ``not_applicable`` do not count. Confident quotes (at least
    ``min_confidence``; a missing confidence never is) decide: all ``present``
    is ``met``, all ``absent`` is ``unmet``, both is ``conflicting``. Without a
    confident quote the requirement is ``unconfirmed``: ``low_confidence`` when
    a quote has a weak verdict, ``not_assessed`` when quotes lack a verdict,
    ``no_mention`` when there is nothing relevant.

    Args:
        requirement: The requirement.
        quotes: Verbatim quotes of the offer with their assessment.
        min_confidence: Threshold of a decisive assessment.

    Returns:
        The check; ``met`` and ``unmet`` carry the most confident quote.
    """
    relevant = [q for q in quotes if q.verdict != "not_applicable"]
    judged = [q for q in relevant if q.verdict is not None]
    confident = [
        q for q in judged if q.confidence is not None and q.confidence >= min_confidence
    ]
    statuses = {_DECISIVE[q.verdict] for q in confident if q.verdict in _DECISIVE}
    if len(statuses) > 1:
        return _unconfirmed(requirement, UnconfirmedReason.CONFLICTING)
    if statuses:
        best = _best(confident)
        return _check(
            requirement, statuses.pop(), quote=best.text, confidence=best.confidence
        )
    if judged:
        return _unconfirmed(
            requirement, UnconfirmedReason.LOW_CONFIDENCE, _best(judged)
        )
    if relevant:
        return _unconfirmed(requirement, UnconfirmedReason.NOT_ASSESSED, relevant[0])
    return _unconfirmed(requirement, UnconfirmedReason.NO_MENTION)


def evaluate_platform(
    requirement: Requirement, host: str | None, allowed: Collection[str]
) -> RequirementCheck:
    """Classify a platform requirement by the link's domain alone.

    Platform requirements of one hardness form a set of allowed platforms
    ("only Airbnb or Booking"), like the search links do.

    Args:
        requirement: A platform requirement.
        host: Host of the offer's link; None without a link.
        allowed: Platform keys of the requirements with the same hardness.

    Returns:
        ``met`` or ``unmet`` quoting the domain; ``no_link`` without a link.
    """
    if host is None:
        return _unconfirmed(requirement, UnconfirmedReason.NO_LINK)
    platform = platform_of(host)
    status = (
        RequirementStatus.MET
        if platform is not None and platform.value in allowed
        else RequirementStatus.UNMET
    )
    return _check(requirement, status, quote=host)


def _amenity(
    requirement: Requirement, facts: OfferFacts, min_confidence: float
) -> RequirementCheck:
    features = facts.features
    if requirement.feature in features.present | features.absent:
        return evaluate(requirement, features)
    if requirement.feature not in facts.requested:
        return _unconfirmed(requirement, UnconfirmedReason.NOT_CHECKED)
    if facts.failed:
        return _unconfirmed(requirement, UnconfirmedReason.CHECK_FAILED)
    if facts.evidence is None:
        return _unconfirmed(requirement, UnconfirmedReason.PENDING)
    quotes = facts.evidence.get(requirement.feature, ())
    return evaluate_evidence(requirement, quotes, min_confidence=min_confidence)


def check_offer(
    requirements: Sequence[Requirement],
    facts: OfferFacts,
    *,
    min_confidence: float = MIN_CONFIDENCE,
) -> list[RequirementCheck]:
    """Evaluate every requirement of a trip against one offer.

    Amenities: the host's answer first, then the worker's quotes. Platforms:
    the link's domain. Distances: nothing in a pasted offer tells, so they stay
    ``not_checked``.

    Args:
        requirements: The trip's requirements.
        facts: What is known about the offer.
        min_confidence: Threshold of a decisive assessment.

    Returns:
        One check per requirement, in the given order.
    """
    platforms = {
        hard: {
            r.feature
            for r in requirements
            if r.kind is RequirementKind.PLATFORM and r.hard is hard
        }
        for hard in (True, False)
    }
    checks: list[RequirementCheck] = []
    for requirement in requirements:
        match requirement.kind:
            case RequirementKind.PLATFORM:
                allowed = platforms[requirement.hard]
                checks.append(evaluate_platform(requirement, facts.host, allowed))
            case RequirementKind.DISTANCE:
                checks.append(_unconfirmed(requirement, UnconfirmedReason.NOT_CHECKED))
            case RequirementKind.AMENITY:
                checks.append(_amenity(requirement, facts, min_confidence))
    return checks


def lodging_score(checks: Sequence[RequirementCheck]) -> float:
    """``S_h`` of E2: product over hard requirements times the mean over soft ones.

    Args:
        checks: Checks of one offer.

    Returns:
        A score in 0..1 (1 without requirements; an empty mean counts as 1).
    """
    hard = [_POINTS[c.status] for c in checks if c.hard]
    soft = [_POINTS[c.status] for c in checks if not c.hard]
    return prod(hard) * (sum(soft) / len(soft) if soft else 1.0)
