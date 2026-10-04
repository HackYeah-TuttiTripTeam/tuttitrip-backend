"""Rule ``accommodation_requirements``: every night has an offer meeting the hard ones.

For each night the best offer covering it is taken (fewest hard requirements
``unmet``, then fewest ``unconfirmed``): one bed per night, so requirements are
never mixed across offers. A hard ``unmet`` is a violation; a hard
``unconfirmed`` (also a night without any offer) is a warning. Soft
requirements only lower ``S_h`` and are not reported here.
"""

from collections.abc import Sequence
from datetime import date

from tuttitrip.accommodation.schemas import (
    RequirementCheck,
    RequirementStatus,
    UnconfirmedReason,
)
from tuttitrip.planning.linter.logic.rule import Rule
from tuttitrip.planning.linter.schemas import (
    Finding,
    LintContext,
    LintOffer,
    LintPlan,
    Severity,
)

CODE = "accommodation_requirements"

REASONS: dict[UnconfirmedReason, str] = {
    UnconfirmedReason.NO_MENTION: "the offer does not mention it",
    UnconfirmedReason.LOW_CONFIDENCE: "the quote is not convincing",
    UnconfirmedReason.NOT_ASSESSED: "the quote was not assessed",
    UnconfirmedReason.CONFLICTING: "the quotes contradict each other",
    UnconfirmedReason.NO_LINK: "the offer has no link",
    UnconfirmedReason.NOT_CHECKED: "not checked",
    UnconfirmedReason.PENDING: "the check is still running",
    UnconfirmedReason.CHECK_FAILED: "the check failed",
    UnconfirmedReason.NO_OFFER: "no offer for this night",
}
"""Readable reasons for the messages (clients map ``reason`` codes themselves)."""


def _hard_checks(offer: LintOffer, hard: Sequence[str]) -> list[RequirementCheck]:
    by_key = {c.feature: c for c in offer.checks}
    return [
        by_key.get(key)
        or RequirementCheck(
            feature=key,
            status=RequirementStatus.UNCONFIRMED,
            reason=UnconfirmedReason.NOT_CHECKED,
        )
        for key in hard
    ]


def _rank(checks: Sequence[RequirementCheck]) -> tuple[int, int]:
    statuses = [c.status for c in checks]
    return statuses.count(RequirementStatus.UNMET), statuses.count(
        RequirementStatus.UNCONFIRMED
    )


def _finding(night: date, check: RequirementCheck) -> Finding | None:
    if check.status is RequirementStatus.UNMET:
        quote = f': "{check.quote}"' if check.quote else ""
        message = f"Lodging does not meet the hard requirement {check.feature}{quote}"
        return Finding(
            rule=CODE, severity=Severity.VIOLATION, message=message, day=night
        )
    if check.status is RequirementStatus.UNCONFIRMED:
        reason = REASONS[check.reason or UnconfirmedReason.NO_MENTION]
        message = f"Hard requirement {check.feature} is unconfirmed: {reason}"
        return Finding(rule=CODE, severity=Severity.WARNING, message=message, day=night)
    return None


def check(_plan: LintPlan, context: LintContext) -> list[Finding]:
    """Report hard lodging requirements per night.

    Args:
        _plan: The plan (unused: the nights come from the context).
        context: Lodging nights, requirements and offers.

    Returns:
        Violations for hard ``unmet``, warnings for hard ``unconfirmed``.
    """
    lodging = context.lodging
    if lodging is None:
        return []
    hard = [r.key for r in lodging.requirements if r.hard]
    if not hard:
        return []
    findings: list[Finding] = []
    for night in sorted(set(lodging.nights)):
        options = [
            _hard_checks(offer, hard)
            for offer in lodging.offers
            if night in offer.nights
        ]
        if options:
            best = min(options, key=_rank)
        else:
            best = [
                RequirementCheck(
                    feature=key,
                    status=RequirementStatus.UNCONFIRMED,
                    reason=UnconfirmedReason.NO_OFFER,
                )
                for key in hard
            ]
        findings.extend(f for c in best if (f := _finding(night, c)) is not None)
    return findings


RULE = Rule(CODE, 4, check)
