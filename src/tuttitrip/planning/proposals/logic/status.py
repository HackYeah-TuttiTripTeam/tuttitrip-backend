"""Status and counts of a proposal. Pure.

Only members with an account answer here; a person without an account is
shown apart (the voting link collects their opinion).
"""

from collections.abc import Collection, Mapping

from tuttitrip.planning.proposals.schemas import (
    ProposalDecision,
    ProposalStatus,
    ProposalTally,
)


def tally(
    required: Collection[str], decisions: Mapping[str, ProposalDecision]
) -> ProposalTally:
    """Count the answers of the members who must answer.

    Args:
        required: Auth0 subjects of everybody with an account on the trip.
        decisions: The decision of each member who answered. Answers of people
            who left the trip are ignored.

    Returns:
        The counts; ``members`` is at least 1 (the host).
    """
    given = [d for sub, d in decisions.items() if sub in required]
    return ProposalTally(
        members=max(1, len(required)),
        approvals=given.count(ProposalDecision.APPROVE),
        rejections=given.count(ProposalDecision.REJECT),
        comments=given.count(ProposalDecision.COMMENT),
        waiting=len(required) - len(given),
    )


def status_of(counts: ProposalTally, *, outdated: bool) -> ProposalStatus:
    """The status of the proposal as a whole.

    Args:
        counts: The counts from ``tally``.
        outdated: The plan changed after the proposal was sent.

    Returns:
        ``outdated``, else ``approved`` when everybody approved, else
        ``rejected`` when somebody rejects, else ``pending``.
    """
    if outdated:
        return ProposalStatus.OUTDATED
    if counts.approvals == counts.members:
        return ProposalStatus.APPROVED
    if counts.rejections:
        return ProposalStatus.REJECTED
    return ProposalStatus.PENDING
