"""Counts and status of a plan proposal."""

import pytest
from pydantic import ValidationError

from tuttitrip.planning.proposals.logic.status import status_of, tally
from tuttitrip.planning.proposals.schemas import (
    ProposalDecision,
    ProposalStatus,
    ResponseCreate,
)

A, B, C = "auth0|a", "auth0|b", "auth0|c"
APPROVE, REJECT, COMMENT = (
    ProposalDecision.APPROVE,
    ProposalDecision.REJECT,
    ProposalDecision.COMMENT,
)


def test_everybody_with_an_account_approving_is_approved() -> None:
    counts = tally([A, B, C], {A: APPROVE, B: APPROVE, C: APPROVE})
    assert (counts.members, counts.approvals, counts.waiting) == (3, 3, 0)
    assert status_of(counts, outdated=False) is ProposalStatus.APPROVED


def test_counts_approvals_rejections_comments_and_waiting() -> None:
    counts = tally([A, B, C], {A: APPROVE, B: REJECT})
    assert (counts.approvals, counts.rejections, counts.comments) == (1, 1, 0)
    assert counts.waiting == 1
    assert status_of(counts, outdated=False) is ProposalStatus.REJECTED


def test_a_comment_is_not_an_approval() -> None:
    counts = tally([A, B], {A: APPROVE, B: COMMENT})
    assert counts.comments == 1
    assert status_of(counts, outdated=False) is ProposalStatus.PENDING


def test_a_member_who_left_is_ignored() -> None:
    counts = tally([A], {A: APPROVE, B: REJECT})
    assert (counts.members, counts.rejections) == (1, 0)
    assert status_of(counts, outdated=False) is ProposalStatus.APPROVED


def test_outdated_wins_over_everything() -> None:
    counts = tally([A], {A: APPROVE})
    assert status_of(counts, outdated=True) is ProposalStatus.OUTDATED


def test_nobody_answered_is_pending() -> None:
    assert status_of(tally([A, B], {}), outdated=False) is ProposalStatus.PENDING


def test_a_comment_needs_a_remark() -> None:
    with pytest.raises(ValidationError):
        ResponseCreate(decision=COMMENT)
    assert ResponseCreate(decision=COMMENT, remark="Za późno").remark == "Za późno"
    assert ResponseCreate(decision=REJECT).remark is None
